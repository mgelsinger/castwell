"""Checks for safe, reproducible model launching, without downloading weights."""

import hashlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location("local_ai", Path(__file__).parents[1] / "scripts" / "local_ai.py")
local_ai = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_ai)


def test_model_integrity_rejects_modified_artifact(tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"verified model")
    checksum = hashlib.sha256(model.read_bytes()).hexdigest()
    local_ai.verify_model(model, checksum)
    model.write_bytes(b"modified model")
    with pytest.raises(ValueError, match="checksum verification failed"):
        local_ai.verify_model(model, checksum)


def test_launcher_uses_loopback_and_handles_model_paths_with_spaces(tmp_path):
    model = tmp_path / "my model.gguf"
    model.touch()
    command = local_ai.server_command(model, port=8123, threads=2)
    assert command[command.index("--model") + 1] == str(model.resolve())
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--model_alias") + 1] == "castwell-local"
    assert command[command.index("--port") + 1] == "8123"
    assert command[command.index("--n_threads") + 1] == "2"
    assert command[command.index("--n_gpu_layers") + 1] == "0"


def test_native_launcher_uses_local_binary_and_loopback_gpu_defaults(tmp_path):
    model = tmp_path / "my model.gguf"
    binary = tmp_path / "my llama server.exe"
    model.touch()
    binary.touch()
    command = local_ai.server_command(model, server_binary=binary, port=8123, threads=8)
    assert command[0] == str(binary.resolve())
    assert command[command.index("--model") + 1] == str(model.resolve())
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--alias") + 1] == "castwell-local"
    assert command[command.index("--port") + 1] == "8123"
    assert command[command.index("--n-gpu-layers") + 1] == "all"
    assert command[command.index("--ctx-size") + 1] == "8192"
    assert command[command.index("--parallel") + 1] == "1"
    assert command[command.index("--threads") + 1] == "8"


def test_native_launcher_resolves_path_and_allows_cpu_mode(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    binary = tmp_path / "llama-server"
    model.touch()
    binary.touch()
    monkeypatch.setattr(local_ai.shutil, "which", lambda name: str(binary))
    command = local_ai.server_command(model, backend="native", gpu_layers=0)
    assert command[0] == str(binary.resolve())
    assert command[command.index("--n-gpu-layers") + 1] == "0"
    monkeypatch.setattr(local_ai.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="not found"):
        local_ai.server_command(model, backend="native")


def test_native_main_does_not_require_python_llama_package(tmp_path, monkeypatch):
    model = tmp_path / "model.gguf"
    binary = tmp_path / "llama-server"
    model.touch()
    binary.touch()
    monkeypatch.setitem(sys.modules, "llama_cpp", None)
    monkeypatch.setattr(sys, "argv", ["local_ai.py", "--model", str(model), "--server-binary", str(binary)])
    launched = []
    monkeypatch.setattr(local_ai.os, "execv", lambda executable, arguments: launched.append((executable, arguments)))
    assert local_ai.main() == 0
    assert launched[0][0] == str(binary.resolve())
    assert launched[0][1][0] == str(binary.resolve())


def test_qwen3_preset_bounds_thinking_and_preserves_final_content(tmp_path):
    model = tmp_path / "Qwen3.gguf"
    binary = tmp_path / "llama-server"
    model.touch()
    binary.touch()
    command = local_ai.server_command(model, server_binary=binary, preset="qwen3")
    assert command[command.index("--reasoning-format") + 1] == "deepseek"
    assert command[command.index("--reasoning-budget") + 1] == "1536"
    assert command[command.index("--n-predict") + 1] == "4096"
    assert command[command.index("--temp") + 1] == "0.6"
    assert command[command.index("--min-p") + 1] == "0"
    no_thinking = local_ai.server_command(model, server_binary=binary, preset="qwen3", thinking=False)
    assert no_thinking[no_thinking.index("--reasoning") + 1] == "off"
    assert no_thinking[no_thinking.index("--temp") + 1] == "0.7"
    assert no_thinking[no_thinking.index("--top-p") + 1] == "0.8"
    for settings in ({"reasoning_budget": -1}, {"reasoning_budget": 4096},
                     {"max_tokens": 8192}, {"context": 2048}):
        with pytest.raises(ValueError, match="reasoning budget"):
            local_ai.server_command(model, server_binary=binary, preset="qwen3", **settings)
    with pytest.raises(ValueError, match="native backend"):
        local_ai.server_command(model, preset="qwen3")


def test_missing_model_and_invalid_resource_settings_fail_before_start(tmp_path):
    model = tmp_path / "missing.gguf"
    with pytest.raises(ValueError, match="does not exist"):
        local_ai.server_command(model)
    model.touch()
    for settings in ({"port": 0}, {"port": 65536}, {"threads": 0}, {"context": 1},
                     {"gpu_layers": -2}, {"backend": "unknown"},
                     {"backend": "python", "server_binary": model}):
        with pytest.raises(ValueError):
            local_ai.server_command(model, **settings)


def test_split_download_verifies_every_shard_before_returning(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_XET_CACHE", str(tmp_path / "xet"))
    names = ["model-00001-of-00002.gguf", "model-00002-of-00002.gguf"]
    checksum = hashlib.sha256(b"verified").hexdigest()
    profile = {"repository": "official/model", "revision": "pinned-revision",
               "directory": "model", "size_gb": 1,
               "files": [(name, checksum) for name in names]}
    monkeypatch.setattr(local_ai, "MODEL_PROFILES", {"7b": profile})
    calls = []

    def download(**kwargs):
        calls.append(kwargs)
        path = Path(kwargs["local_dir"]) / kwargs["filename"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"verified" if kwargs["filename"] == names[0] else b"corrupted")
        return str(path)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(hf_hub_download=download))
    with pytest.raises(ValueError, match="checksum verification failed"):
        local_ai.download_model(tmp_path)
    assert len(calls) == 2
    assert all(call["revision"] == "pinned-revision" for call in calls)
