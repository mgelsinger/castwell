"""CUDA inference must fail visibly instead of switching to CPU models."""
import sys
import types
from unittest.mock import Mock

import pytest

from castwell import gpu, processing


def test_whisper_uses_cuda_and_does_not_retry_on_cpu(monkeypatch):
    factory = Mock(side_effect=RuntimeError("insufficient GPU memory"))
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=factory))
    monkeypatch.setattr(gpu, "configure_cuda_vad", lambda: None)
    processing._cached_model.cache_clear()
    with pytest.raises(RuntimeError, match="GPU memory"):
        processing._cached_model("small.en", "cache")
    factory.assert_called_once_with("small.en", device="cuda", compute_type="float16", download_root="cache")
    processing._cached_model.cache_clear()


def vad_dependencies(monkeypatch, providers):
    options = Mock()
    session = Mock()
    session.get_providers.return_value = providers
    runtime = types.SimpleNamespace(
        get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
        SessionOptions=lambda: options,
        InferenceSession=Mock(return_value=session),
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", runtime)
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", types.SimpleNamespace(get_assets_path=lambda: "assets"))
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", types.SimpleNamespace(SileroVADModel=type("VAD", (), {})))
    gpu.cuda_vad_model.cache_clear()
    return runtime, options, session


def test_vad_disables_cpu_fallback_and_accepts_only_cuda(monkeypatch):
    runtime, options, session = vad_dependencies(monkeypatch, ["CUDAExecutionProvider", "CPUExecutionProvider"])
    try:
        assert gpu.cuda_vad_model().session is session
        options.add_session_config_entry.assert_called_once_with("session.disable_cpu_ep_fallback", "1")
        assert runtime.InferenceSession.call_args.kwargs["providers"] == [("CUDAExecutionProvider", {"device_id": 0})]
        session.disable_fallback.assert_called_once()
    finally:
        gpu.cuda_vad_model.cache_clear()


def test_vad_rejects_provider_that_falls_back_to_cpu(monkeypatch):
    vad_dependencies(monkeypatch, ["CPUExecutionProvider"])
    with pytest.raises(RuntimeError, match="exclusively on NVIDIA CUDA"):
        gpu.cuda_vad_model()
    gpu.cuda_vad_model.cache_clear()


def test_vad_rejects_cpu_only_runtime_before_model_load(monkeypatch):
    runtime, _, _ = vad_dependencies(monkeypatch, ["CPUExecutionProvider"])
    runtime.get_available_providers = lambda: ["CPUExecutionProvider"]
    with pytest.raises(RuntimeError, match="onnxruntime-gpu"):
        gpu.cuda_vad_model()
    runtime.InferenceSession.assert_not_called()
    gpu.cuda_vad_model.cache_clear()
