"""The Turkish evaluation set (Phase 6 brief A4/A5, branch 3a).

- `scorer`: WER with Turkish normalisation (case incl. `İ/ı`, punctuation).
- `manifest`: the committed manifest (`docs/voice/eval-manifest.md`) and the
  owner's private set directory (outside the repo).
- `replay`: the recorded-audio replay harness (capture/segment/STT/TTS
  ports), latency statistics and the stored baseline.
- `runner`: targets, the WER run, the TTS listening sheet, trace latency.
- `fixtures`: synthetic tone/silence clips (CI; the self-trigger fixture).

The only entry point is `manage.py voice_eval`.
"""
