"""NVIDIA-only speech inference, including the speech activity model."""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import sys

_DLL_HANDLES = []


@lru_cache(maxsize=1)
def configure_cuda_runtime() -> None:
    """Use bundled CUDA DLLs or the explicit development runtime directory."""
    if os.name != "nt":
        return
    specified = os.getenv("CASTWELL_CUDA_DIR")
    if specified:
        directory = Path(specified).expanduser().resolve()
    elif getattr(sys, "frozen", False):
        directory = Path(sys._MEIPASS) / "cuda"
    else:
        directory = Path(__file__).resolve().parents[1] / ".local" / "windows-vendor-cuda" / "cuda-runtime"
    if directory.is_dir():
        os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
        _DLL_HANDLES.append(os.add_dll_directory(str(directory)))


@lru_cache(maxsize=1)
def cuda_vad_model():
    configure_cuda_runtime()
    import onnxruntime
    from faster_whisper.utils import get_assets_path
    from faster_whisper.vad import SileroVADModel

    if "CUDAExecutionProvider" not in onnxruntime.get_available_providers():
        raise RuntimeError("NVIDIA CUDA speech activity detection requires onnxruntime-gpu.")
    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3
    options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
    # Preserve the upstream Silero preprocessing and timestamp behavior, while
    # replacing its constructor's hardcoded CPU execution provider.
    model = SileroVADModel.__new__(SileroVADModel)
    model.session = onnxruntime.InferenceSession(
        str(Path(get_assets_path()) / "silero_vad_v6.onnx"),
        sess_options=options,
        providers=[("CUDAExecutionProvider", {"device_id": 0})],
    )
    model.session.disable_fallback()
    # ONNX Runtime lists its registered CPU provider even when the session
    # configuration prohibits assigning graph nodes to it. CUDA must remain
    # first and CPU graph fallback is disabled above.
    if model.session.get_providers()[0:1] != ["CUDAExecutionProvider"]:
        raise RuntimeError("Speech activity detection did not initialize exclusively on NVIDIA CUDA.")
    return model


def configure_cuda_vad() -> None:
    """Install the CUDA factory before any Whisper transcription request."""
    configure_cuda_runtime()
    import faster_whisper.vad
    faster_whisper.vad.get_vad_model = cuda_vad_model
