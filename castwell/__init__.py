"""Castwell: a personal podcast library."""

import os

# Set this before Whisper's VAD imports ONNX Runtime. Keep processing local and
# avoid analytics session files appearing in a user's working directory.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

__version__ = "0.4.0"
