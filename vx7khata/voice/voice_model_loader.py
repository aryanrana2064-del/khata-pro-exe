"""Finding and checking the voice models that are actually installed.

A voice is two files that belong together: ``<name>.onnx`` (the model) and ``<name>.onnx.json`` (its config).
Models are looked for in the user's data folder first and the bundled assets folder second, because
``%APPDATA%\\VX7 KHATA PRO`` survives an upgrade while Program Files is replaced wholesale by the installer -
that is where a customer drops a Hindi voice later without it being deleted by the next update.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths
from .voice_settings import LANGUAGE_ENGLISH, LANGUAGE_HINDI, LANGUAGE_HINGLISH

MIN_MODEL_BYTES = 1_000_000      # anything smaller is a partial download, not a voice
BUNDLED_DIRNAME = "voice"
# A folder voice is a whole bundle: four ONNX graphs, a tokenizer and the recorded voice caches.
BUNDLE_GRAPHS = ("text_conditioner.onnx", "flow_main_weight_int8.onnx", "flow_one_step.onnx", "mimi_decoder.onnx")


def _language_of(voice_id: str) -> str:
    head = voice_id.split("_", 1)[0].lower()
    if head == "hi":
        return LANGUAGE_HINDI
    if head.startswith("en"):
        return LANGUAGE_ENGLISH
    return LANGUAGE_ENGLISH


@dataclass
class VoiceModel:
    name: str
    label: str
    model: Path
    config: Path
    language: str = LANGUAGE_ENGLISH
    gender: str = "female"
    license: str = ""
    speakers: int = 1
    speakers_by_gender: dict[str, int] = field(default_factory=dict)
    usable: bool = True
    reason: str = ""

    @property
    def key(self) -> str:
        return self.name

    def speaker_for(self, gender: str | None) -> int:
        """The engine id of a speaker with that gender, or the first one when the voice cannot tell them apart.

        A multi-speaker model holds hundreds of real people, so a gender is a speaker id inside the model rather
        than a property of the model; the ids we publish come from the documented LibriTTS speaker lists.
        """
        if gender is not None:
            found = self.speakers_by_gender.get(gender)
            if found is not None:
                return int(found)
        return 0


@dataclass
class VoiceLibrary:
    """Everything the loader found, plus why the assistant may be silent."""

    models: list[VoiceModel] = field(default_factory=list)
    roots: tuple[Path, ...] = ()
    engine_available: bool = False
    engine_error: str = ""

    def for_language(self, language: str, gender: str | None = None) -> list[VoiceModel]:
        # Hinglish rides on the Hindi voice: it is Hindi words first, with a few English ones inside.
        wanted = {LANGUAGE_HINGLISH: {LANGUAGE_HINDI, LANGUAGE_ENGLISH},
                  LANGUAGE_HINDI: {LANGUAGE_HINDI},
                  LANGUAGE_ENGLISH: {LANGUAGE_ENGLISH}}.get(language, {LANGUAGE_ENGLISH})
        found = [m for m in self.models if m.usable and m.language in wanted]
        if language == LANGUAGE_HINGLISH:
            found.sort(key=lambda m: m.language != LANGUAGE_HINDI)
        if gender:
            whole = [m for m in found if m.gender == gender]          # a voice that is only that gender
            if whole:
                return whole
            mixed = [m for m in found if gender in m.speakers_by_gender]  # a speaker inside a many-voiced model
            if mixed:
                return mixed
        return found

    def pick(self, language: str, gender: str | None = None) -> VoiceModel | None:
        options = self.for_language(language, gender) or self.for_language(language)
        return options[0] if options else None

    @property
    def status(self) -> str:
        if not self.engine_available:
            return self.engine_error or "The speech engine is not installed."
        if not any(m.usable for m in self.models):
            return "No voice model is installed."
        return ""


def bundled_dir() -> Path:
    return paths.asset_path(BUNDLED_DIRNAME)


def user_dir() -> Path:
    return paths.data_dir() / BUNDLED_DIRNAME


def _manifest(root: Path) -> dict:
    """Optional ``voices.json`` giving language, gender and licence per file - the model config has none."""
    file = root / "voices.json"
    if not file.is_file():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _scan(root: Path) -> list[VoiceModel]:
    if not root.is_dir():
        return []
    manifest = _manifest(root)
    found = []
    for model in sorted(root.glob("*.onnx")):
        name = model.name[:-len(".onnx")]
        entry = manifest.get(name, {}) if isinstance(manifest.get(name), dict) else {}
        config = model.with_suffix(".onnx.json")
        reason, usable, info = "", True, {}
        if model.stat().st_size < MIN_MODEL_BYTES:
            usable, reason = False, "the model file is incomplete"
        elif not config.is_file():
            usable, reason = False, "its configuration file is missing"
        else:
            try:
                parsed = json.loads(config.read_text(encoding="utf-8"))
                info = parsed if isinstance(parsed, dict) else {}
            except (OSError, ValueError):
                usable, reason = False, "its configuration file is damaged"
        language = str(entry.get("language") or _language_of(name))
        found.append(VoiceModel(
            name=name,
            label=str(entry.get("label") or name),
            model=model,
            config=config,
            language=language,
            gender=str(entry.get("gender") or "female"),
            license=str(entry.get("license") or ""),
            speakers=max(1, _count(entry.get("speakers")) or _count(info.get("num_speakers")) or 1),
            speakers_by_gender=_gender_speakers(entry),
            usable=usable,
            reason=reason,
        ))
    found.extend(_scan_bundles(root, manifest))
    return found


def _scan_bundles(root: Path, manifest: dict) -> list[VoiceModel]:
    """Voice folders: <name>/bundle.json plus the graphs and the voice caches beside it."""
    found = []
    for bundle in sorted(root.glob("*/bundle.json")):
        folder = bundle.parent
        name = folder.name
        entry = manifest.get(name, {}) if isinstance(manifest.get(name), dict) else {}
        reason, usable = "", True
        for graph in BUNDLE_GRAPHS:
            file = folder / graph
            if not file.is_file():
                usable, reason = False, f"its {graph} is missing"
                break
            if file.stat().st_size < MIN_MODEL_BYTES:
                usable, reason = False, "the model file is incomplete"
                break
        if usable and not (folder / "tokenizer.model").is_file():
            usable, reason = False, "its tokenizer is missing"
        found.append(VoiceModel(
            name=name,
            label=str(entry.get("label") or name),
            model=folder,
            config=bundle,
            language=str(entry.get("language") or _language_of(name)),
            gender=str(entry.get("gender") or "female"),
            license=str(entry.get("license") or ""),
            speakers=max(1, _count(entry.get("speakers")) or 1),
            speakers_by_gender=_gender_speakers(entry),
            usable=usable,
            reason=reason,
        ))
    return found


def _count(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _gender_speakers(entry: dict) -> dict[str, int]:
    """``{"male": 805, "female": 307}`` from the manifest, with anything uncountable dropped."""
    raw = entry.get("gender_speakers")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for gender, speaker in raw.items():
        try:
            out[str(gender)] = int(speaker)
        except (TypeError, ValueError):
            continue
    return out


def discover(engine_available: bool = False, engine_error: str = "") -> VoiceLibrary:
    """Every voice the assistant can use right now, user folder first."""
    roots = (user_dir(), bundled_dir())
    models: list[VoiceModel] = []
    seen: set[str] = set()
    for root in roots:
        for model in _scan(root):
            if model.name not in seen:
                seen.add(model.name)
                models.append(model)
    return VoiceLibrary(models=models, roots=roots, engine_available=engine_available, engine_error=engine_error)
