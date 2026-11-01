"""Hindi speech sidecar - the second speech engine, for the voice Piper cannot give us.

Every Piper Hindi voice is CC BY-NC-SA, so it cannot ship in a paid installer. This sidecar instead runs
Indus Pocket TTS (Hindi/Hinglish, CC BY 4.0) from a folder of ONNX graphs with onnxruntime only - no torch, no
network. It is a separate process for the same reasons as the Piper one: a model crash must not take the ledger
down, and the model is loaded once for the whole session.

Protocol (JSON lines), identical to the Piper sidecar so the ledger cannot tell them apart:

    ->  {"ready": true}                              once the graphs and the voice caches are loaded
    <-  {"text": …, "out": …}                        a sentence to speak
    ->  {"ok": true, "out": "…wav"}  or  {"ok": false, "error": "…"}

The generation loop follows the bundle's own graph contract: text conditioner -> flow prefill with Inaya's voice
caches -> per-step flow -> Mimi decoder -> PCM16 WAV at 24 kHz.
"""
from __future__ import annotations

import json
import math
import os
import re
import struct
import sys
import wave
from pathlib import Path

from .piper_sidecar import _answer, _open_pipes

SAMPLE_RATE = 24000
LATENT = 32
D_MODEL = 1024
LAYERS = 6
NOISE_STD = math.sqrt(0.3)     # the contract's sampling floor
EOS_THRESHOLD = -4.0
FRAMES_AFTER_EOS = 3
MAX_TOKENS_PER_CHUNK = 32
MAX_FRAMES_PER_CHUNK = 250
MIN_FRAMES_PER_CHUNK = 75


def _read_voice_caches(path: Path) -> list:
    """The six attention caches from ``inaya.safetensors``.

    The file is a tiny header plus a flat float32 buffer, so it is read directly instead of pulling in the
    safetensors package for one file that never changes.
    """
    import numpy as np

    raw = path.read_bytes()
    header_len = struct.unpack("<Q", raw[:8])[0]
    header = json.loads(raw[8:8 + header_len])
    body = raw[8 + header_len:]
    caches = []
    for layer in range(LAYERS):
        key = f"transformer.layers.{layer}.self_attn/cache"
        info = header.get(key)
        if not info or info.get("dtype") != "F32":
            raise ValueError(f"the voice file has no usable {key}")
        start, end = info["data_offsets"]
        shape = [int(x) for x in info["shape"]]
        caches.append(np.frombuffer(body[start:end], dtype="<f4").reshape(shape).copy())
    return caches


def _chunks(tokenizer, text: str) -> list:
    """Split into the <=32-token chunks the graphs were exported for, preferring punctuation boundaries."""
    import numpy as np

    sentences = [s.strip() for s in re.split(r"[।.!?;,\n]+", text) if s.strip()]
    chunks = []
    for sentence in sentences:
        tokens = tokenizer.Encode(sentence, out_type=int)
        if len(tokens) <= MAX_TOKENS_PER_CHUNK:
            chunks.append(np.array(tokens, dtype=np.int64))
            continue
        current = []
        for word in sentence.split():
            piece = tokenizer.Encode(word, out_type=int)
            if current and len(current) + len(piece) > MAX_TOKENS_PER_CHUNK:
                chunks.append(np.array(current, dtype=np.int64))
                current = []
            current.extend(piece)
        if current:
            chunks.append(np.array(current, dtype=np.int64))
    return [c for c in chunks if len(c)]


class HindiVoice:
    """One loaded Hindi model folder; ``speak`` turns a sentence into a WAV file."""

    def __init__(self, folder: Path):
        import numpy as np
        import onnxruntime as ort
        from sentencepiece import SentencePieceProcessor

        self.folder = folder
        self.bundle = json.loads((folder / "bundle.json").read_text(encoding="utf-8"))
        self.tokenizer = SentencePieceProcessor()
        self.tokenizer.Load(str(folder / "tokenizer.model"))
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.enable_mem_pattern = False
        provider = ["CPUExecutionProvider"]
        self.text = ort.InferenceSession(str(folder / "text_conditioner.onnx"), options, providers=provider)
        self.main = ort.InferenceSession(str(folder / "flow_main_weight_int8.onnx"), options, providers=provider)
        self.flow = ort.InferenceSession(str(folder / "flow_one_step.onnx"), options, providers=provider)
        self.decoder = ort.InferenceSession(str(folder / "mimi_decoder.onnx"), options, providers=provider)
        self._outputs = [o.name for o in self.main.get_outputs()]
        self._caches = _read_voice_caches(folder / "inaya.safetensors")
        self._zero_states = [np.zeros([int(x) for x in m["shape"]], np.float32)
                             for m in self.bundle["mimi_state"]]

    def _chunk(self, tokens) -> "object":
        import numpy as np

        rng = np.random.default_rng(1234)      # a fixed seed keeps a sentence repeatable, flaw and all
        embeddings = self.text.run(None, {"tokens": tokens.reshape(1, -1)})[0]
        feeds = {"sequence": np.zeros((1, 0, LATENT), np.float32), "text_embeddings": embeddings}
        caches = self._run_main(feeds)
        rng.standard_normal((1, LATENT), np.float32)   # the contract draws one noise tensor per chunk prefill

        budget = min(MAX_FRAMES_PER_CHUNK, max(MIN_FRAMES_PER_CHUNK, 8 * len(tokens) + 50))
        states = self._zero_states
        sequence = np.full((1, 1, LATENT), np.nan, np.float32)
        pieces, first_eos = [], None
        empty = np.zeros((1, 0, D_MODEL), np.float32)
        for step in range(budget):
            version, caches = self._run_main({"sequence": sequence, "text_embeddings": empty},
                                             caches=caches, full=True)
            eos = float(np.asarray(version["eos_logit"]).reshape(-1)[0])
            if first_eos is None and eos > EOS_THRESHOLD:
                first_eos = step
            noise = rng.standard_normal((1, LATENT), np.float32) * NOISE_STD
            latent = self.flow.run(None, {"conditioning": version["conditioning"], "noise": noise})[0]
            if first_eos is not None and step >= first_eos + FRAMES_AFTER_EOS:
                break
            feeds = {"latent": latent.reshape(1, 1, LATENT)}
            for j, state in enumerate(states):
                feeds[f"state_{j}"] = state
            decoded = self.decoder.run(None, feeds)
            if not np.isfinite(decoded[0]).all():
                raise ValueError("the Hindi voice produced a broken frame")
            pieces.append(decoded[0])
            states = decoded[1:]
            sequence = latent.reshape(1, 1, LATENT).astype(np.float32)
        return np.concatenate(pieces, -1).reshape(-1)

    def _run_main(self, feeds, caches=None, full=False):
        source = self._caches if caches is None else caches
        for i in range(LAYERS):
            feeds[f"cache_{i}"] = source[i]
        result = self.main.run(self._outputs, feeds)
        named = dict(zip(self._outputs, result))
        present = [named[f"present_{i}"] for i in range(LAYERS)]
        return (named, present) if full else present

    def speak(self, text: str, out: Path) -> None:
        import numpy as np

        pieces = [self._chunk(tokens) for tokens in _chunks(self.tokenizer, text)]
        if not pieces:
            raise ValueError("there was nothing to say")
        audio = np.clip(np.concatenate(pieces), -1.0, 1.0)
        with wave.open(str(out), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes((audio * 32767).astype("<i2").tobytes())


def main(argv: list[str] | None = None) -> int:
    import argparse

    _open_pipes()
    parser = argparse.ArgumentParser(description="Offline Hindi speech sidecar for VX7 KHATA PRO")
    parser.add_argument("--bundle", required=True, help="folder holding the ONNX voice bundle")
    args = parser.parse_args(argv)

    folder = Path(args.bundle)
    try:
        voice = HindiVoice(folder)
    except Exception as exc:  # noqa: BLE001 - the ledger shows this line instead of a traceback
        _answer({"ready": False, "error": f"the Hindi voice could not be loaded: {exc}"})
        return 4
    _answer({"ready": True, "model": str(folder)})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            _answer({"ok": False, "error": "unreadable request"})
            continue
        out = Path(str(request.get("out") or ""))
        if not out.parent.is_dir():
            _answer({"ok": False, "error": "the output folder does not exist"})
            continue
        try:
            voice.speak(str(request.get("text") or ""), out)
        except Exception as exc:  # noqa: BLE001 - one bad sentence must not end the session
            out.unlink(missing_ok=True)
            if os.environ.get("VX7_VOICE_DEBUG"):
                import traceback

                _answer({"ok": False, "error": traceback.format_exc()})
            else:
                _answer({"ok": False, "error": str(exc)})
            continue
        _answer({"ok": True, "out": str(out)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
