"""Reference side of the wake-word parity test (Phase 6 branch 4, step 0).

Manual/nightly only -- never CI. Runs Python openWakeWord's own
`AudioFeatures` (inference_framework="onnx") over a 16 kHz mono PCM16 file and
writes per-chunk embeddings and keyword scores to `<dir>/oww_out.json`, which
`src/lib/voice/wakeword/parity.nightly.test.ts` compares against the
TypeScript port. Setup (outside the repo, in a throwaway venv):

    uv venv /tmp/oww && uv pip install -p /tmp/oww/bin/python \
        onnxruntime numpy scipy tqdm requests scikit-learn
    uv pip install -p /tmp/oww/bin/python --no-deps openwakeword==0.6.0
    /tmp/oww/bin/python -I frontend/scripts/oww-parity.py <dir> [keyword.onnx]

<dir> holds melspectrogram.onnx, embedding_model.onnx, the keyword model and
audio.raw. Without audio.raw a deterministic synthetic signal is written
(tone + noise); for a meaningful score comparison use a real "hey friday"
recording (owner step).
"""

import json
import math
import random
import struct
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from openwakeword.utils import AudioFeatures


def synthetic_audio(path: Path, seconds: int = 4) -> None:
    random.seed(7)
    n = 16000 * seconds
    vals = [
        int(8000 * math.sin(2 * math.pi * 440 * i / 16000) * math.sin(2 * math.pi * 3 * i / 16000))
        + random.randint(-1500, 1500)
        for i in range(n)
    ]
    path.write_bytes(struct.pack(f"<{n}h", *vals))


def main() -> None:
    d = Path(sys.argv[1])
    keyword = sys.argv[2] if len(sys.argv) > 2 else "hey_friday.onnx"
    raw = d / "audio.raw"
    if not raw.exists():
        synthetic_audio(raw)
    audio = np.fromfile(raw, dtype="<i2")
    features = AudioFeatures(
        melspec_model_path=str(d / "melspectrogram.onnx"),
        embedding_model_path=str(d / "embedding_model.onnx"),
        inference_framework="onnx",
    )
    kw = ort.InferenceSession(str(d / keyword))
    out: dict[str, list] = {"embeddings": [], "scores": []}
    for start in range(0, len(audio) - 1279, 1280):
        features(audio[start : start + 1280])
        out["embeddings"].append(features.feature_buffer[-1].tolist())
        feats = features.get_features(16).astype(np.float32)
        out["scores"].append(float(kw.run(None, {kw.get_inputs()[0].name: feats})[0].squeeze()))
    (d / "oww_out.json").write_text(json.dumps(out))
    print(f"{len(out['embeddings'])} chunks written to {d / 'oww_out.json'}")


if __name__ == "__main__":
    main()
