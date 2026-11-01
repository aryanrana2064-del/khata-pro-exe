# Attribution and modification notice

- Original released model: **Indus Labs**, [Indus-Labs/indus-pocket-tts](https://huggingface.co/Indus-Labs/indus-pocket-tts/tree/39899d3c1dcda47a7ef167cd222419feb86abeb9), revision `39899d3c1dcda47a7ef167cd222419feb86abeb9`.
- Base model: **Kyutai**, [Pocket TTS](https://huggingface.co/kyutai/pocket-tts). The publisher attributes the base model to Kyutai and describes its Hindi/Hinglish finetuning, new tokenizer, distillation from 24 to six layers, and removal of the audio encoder.
- Conversion work: **Scribe Animator / OHO Tech**, 2026-10-21. Exported four functional ONNX graphs using the official PyPI `pocket-tts==3.1.0` runtime. Converted recurrent cache updates to explicit graph inputs/outputs. Stored 24 Transformer projection/feed-forward matrices as symmetric INT8 weights per output channel with floating-point dequantization. No new finetuning or voice cloning is claimed.

The ONNX graphs are modified derivatives. `tokenizer.model`, `inaya.safetensors`, and `upstream/config.yaml` retain their upstream bytes. The six-layer fixed-voice architecture and removal of the audio encoder are upstream changes, not new claims by the converter.

The publisher's exact license text is retained in [LICENSE.md](LICENSE.md). The existing release's exact derivative notice is retained in [NOTICE.txt](NOTICE.txt). The model assets retain [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), with the publisher-stated Kyutai use-term notice preserved. No ownership of Indus Labs' or Kyutai's original work is claimed; attribution does not imply sponsorship or endorsement.

No publisher Python scripts/normalizer, private Scribe application source, third-party runtime binaries, user recordings, or project files are distributed in this candidate. The upstream notice's MIT section concerns the publisher code it names; it does not license private application code.
