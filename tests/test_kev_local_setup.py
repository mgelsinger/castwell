"""Optional Kev setup checks using tiny artifacts and no live models."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts/setup_kev_local.py"
LAUNCHER = ROOT / "scripts/start_kev_local.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


@pytest.fixture
def setup(monkeypatch):
    spec = importlib.util.spec_from_file_location("kev_setup_test", SETUP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("HF_HOME", "HF_XET_CACHE", "HF_XET_CHUNK_CACHE_SIZE_BYTES", "HF_HUB_DOWNLOAD_TIMEOUT"):
        monkeypatch.setenv(name, "test-original")
    return module


def tiny_cache(setup, tmp_path, monkeypatch, size):
    models = []
    snapshots = {}
    for index in range(2):
        snapshot = tmp_path / f"snapshot-{index}"
        snapshot.mkdir()
        files = {}
        for name in ("config.json", "head.pt", "model.safetensors"):
            content = f"test artifact {size} {index} {name}".encode()
            (snapshot / name).write_bytes(content)
            files[name] = hashlib.sha256(content).hexdigest()
        repo = f"example/{size}-{index}"
        models.append({"repository": repo, "revision": str(index) * 40, "files": files})
        snapshots[repo] = snapshot
    monkeypatch.setitem(setup.MODEL_SETS, size, models)
    calls = []
    def snapshot_download(repo, **options):
        calls.append((repo, options))
        return str(snapshots[repo])
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=snapshot_download))
    return snapshots, calls


def test_setup_is_python310_syntax():
    ast.parse(SETUP.read_text(encoding="utf-8"), feature_version=(3, 10))


def test_profiles_preserve_4b_and_pin_all_loader_artifacts(setup):
    assert setup.MODEL_SETS["4b"] is setup.MODELS
    assert setup.MODELS[0]["revision"] == "6cfce5c2fa4b4bd64026336ab649c5ca78857d52"
    old_adapter, old_base = setup.MODELS
    assert {"adapter_config.json", "added_tokens.json", "special_tokens_map.json",
            "merges.txt", "vocab.json", "tokenizer_config.json"} <= old_adapter["files"].keys()
    assert {"config.json", "model.safetensors.index.json", "merges.txt",
            "vocab.json", "tokenizer_config.json"} <= old_base["files"].keys()
    assert sum(len(model["files"]) for model in setup.MODELS) == 17
    adapter, base = setup.MODEL_SETS["9b"]
    assert adapter["revision"] == "db029f08b290afd9fee4aa4bbcd9ae48602d1eb0"
    assert base["revision"] == "68c46c4b3498877f3ef123c856ecfde50c39f404"
    assert {"adapter_config.json", "adapter_model.safetensors", "head.pt"} <= adapter["files"].keys()
    assert {"config.json", "model.safetensors.index.json", "tokenizer.json", "tokenizer_config.json"} <= base["files"].keys()
    assert len([name for name in base["files"] if name.endswith(".safetensors")]) == 4
    assert sum(len(model["files"]) for model in (adapter, base)) == 16
    assert all(len(digest) == 64 and set(digest) <= set("0123456789abcdef")
               for model in (adapter, base) for digest in model["files"].values())


@pytest.mark.parametrize("size,filename", [("4b", "kev-model-provenance.json"), ("9b", "kev9-model-provenance.json")])
def test_offline_verification_is_pinned_and_keeps_other_model_manifest(setup, tmp_path, monkeypatch, size, filename):
    _, calls = tiny_cache(setup, tmp_path, monkeypatch, size)
    other = tmp_path / ("kev-model-provenance.json" if size == "9b" else "kev9-model-provenance.json")
    other.write_bytes(b"existing unrelated model provenance")
    setup.download_models(tmp_path, size=size)
    result = json.loads((tmp_path / filename).read_text(encoding="utf-8"))
    assert len(result) == 2
    assert sum(len(record["files"]) for record in result) == 6
    assert all(item["sha256_verified"] for record in result for item in record["files"])
    original = (tmp_path / filename).read_bytes()
    calls.clear()
    setup.download_models(tmp_path, offline=True, size=size)
    assert (tmp_path / filename).read_bytes() == original
    assert other.read_bytes() == b"existing unrelated model provenance"
    assert len(calls) == 2
    for (_, options), model in zip(calls, setup.MODEL_SETS[size]):
        assert options["revision"] == model["revision"]
        assert options["local_files_only"] is True
        assert options["token"] is False
        assert Path(options["cache_dir"]) == tmp_path / "models/huggingface/hub"
        assert set(options["allow_patterns"]) == set(model["files"])


@pytest.mark.parametrize("size", ["4b", "9b"])
def test_verification_without_provenance_does_not_create_it(setup, tmp_path, monkeypatch, size):
    tiny_cache(setup, tmp_path, monkeypatch, size)
    setup.download_models(tmp_path, offline=True, size=size)
    assert not (tmp_path / "kev-model-provenance.json").exists()
    assert not (tmp_path / "kev9-model-provenance.json").exists()


@pytest.mark.parametrize("field", ["repository", "revision", "path"])
@pytest.mark.parametrize("record_index", [0, 1])
def test_verification_rejects_unverified_manifest_identity_or_path(setup, tmp_path, monkeypatch, field, record_index):
    tiny_cache(setup, tmp_path, monkeypatch, "4b")
    setup.download_models(tmp_path, size="4b")
    manifest = tmp_path / "kev-model-provenance.json"
    records = json.loads(manifest.read_text(encoding="utf-8"))
    records[record_index][field] = "different identity or path"
    manifest.write_text(json.dumps(records), encoding="utf-8")
    before = manifest.read_bytes()
    with pytest.raises(ValueError, match="verified model paths"):
        setup.download_models(tmp_path, offline=True, size="4b")
    assert manifest.read_bytes() == before


def test_old_manifest_remains_byte_identical_after_full_current_verification(setup, tmp_path, monkeypatch):
    snapshots, calls = tiny_cache(setup, tmp_path, monkeypatch, "4b")
    manifest = tmp_path / "kev-model-provenance.json"
    old_records = [{"repository": model["repository"], "revision": model["revision"],
                    "path": str(snapshots[model["repository"]]), "files": []}
                   for model in setup.MODEL_SETS["4b"]]
    manifest.write_text(json.dumps(old_records, indent=3) + "\n", encoding="utf-8")
    before = manifest.read_bytes()
    setup.download_models(tmp_path, offline=True, size="4b")
    assert manifest.read_bytes() == before
    assert len(calls) == 2


@pytest.mark.parametrize("filename", ["config.json", "head.pt", "model.safetensors"])
@pytest.mark.parametrize("size", ["4b", "9b"])
def test_any_selected_file_corruption_preserves_existing_provenance(setup, tmp_path, monkeypatch, filename, size):
    snapshots, _ = tiny_cache(setup, tmp_path, monkeypatch, size)
    (snapshots[f"example/{size}-1"] / filename).write_bytes(b"corrupted")
    manifest = tmp_path / ("kev-model-provenance.json" if size == "4b" else "kev9-model-provenance.json")
    manifest.write_bytes(b"previous verified provenance")
    with pytest.raises(ValueError, match="Checksum mismatch"):
        setup.download_models(tmp_path, offline=True, size=size)
    assert manifest.read_bytes() == b"previous verified provenance"


def test_real_4b_adapter_config_pin_rejects_changed_loader_before_weights(setup, tmp_path, monkeypatch):
    snapshot = tmp_path / "pinned snapshot"
    snapshot.mkdir()
    # Valid JSON with a changed base identifier must not proceed to model loading.
    (snapshot / "adapter_config.json").write_text('{"base_model_name_or_path":"changed/base"}', encoding="utf-8")
    calls = []
    def snapshot_download(repo, **options):
        calls.append((repo, options))
        return str(snapshot)
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=snapshot_download))
    manifest = tmp_path / "kev-model-provenance.json"
    manifest.write_bytes(b"prior control provenance")
    with pytest.raises(ValueError, match=r"Checksum mismatch: .*adapter_config\.json"):
        setup.download_models(tmp_path, offline=True, size="4b")
    assert calls[0][0] == "jaredpalmer/kev-4b"
    assert calls[0][1]["revision"] == "6cfce5c2fa4b4bd64026336ab649c5ca78857d52"
    assert len(calls) == 1
    assert manifest.read_bytes() == b"prior control provenance"


@pytest.mark.parametrize("arguments,size", [([], "4b"), (["--size", "9b"], "9b")])
def test_verify_cli_selects_size_without_installing(setup, tmp_path, monkeypatch, arguments, size):
    calls = []
    monkeypatch.setattr(setup, "download_models", lambda directory, **options: calls.append((directory, options)))
    monkeypatch.setattr(setup, "checked", lambda *args: pytest.fail("Verification must never install"))
    monkeypatch.setattr(sys, "argv", [str(SETUP), "--verify-only", "--directory", str(tmp_path), *arguments])
    setup.main()
    assert calls == [(tmp_path.resolve(), {"offline": True, "size": size})]


def test_invalid_model_size_rejected_before_download(setup, monkeypatch):
    monkeypatch.setattr(setup, "download_models", lambda *args, **kwargs: pytest.fail("Unexpected download"))
    monkeypatch.setattr(sys, "argv", [str(SETUP), "--verify-only", "--size", "27b"])
    with pytest.raises(SystemExit) as exc:
        setup.main()
    assert exc.value.code == 2


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")
@pytest.mark.parametrize("size", ["4b", "9b"])
@pytest.mark.parametrize("corrupt", [None, "revision", "repository"])
def test_launcher_selects_and_validates_manifest_before_any_port_or_process(tmp_path, size, corrupt):
    local = tmp_path / "local with spaces"
    python = local / "kev-venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"never executed")
    for profile, revision in [("4b", "6cfce5c2fa4b4bd64026336ab649c5ca78857d52"),
                              ("9b", "db029f08b290afd9fee4aa4bbcd9ae48602d1eb0")]:
        checkpoint = local / profile
        checkpoint.mkdir()
        (checkpoint / "head.pt").write_bytes(b"never loaded")
        record = {"repository": f"jaredpalmer/kev-{profile}", "revision": revision, "path": str(checkpoint)}
        if profile == size and corrupt:
            record[corrupt] = "wrong identity"
        name = "kev-model-provenance.json" if profile == "4b" else "kev9-model-provenance.json"
        (local / name).write_text(json.dumps([record]), encoding="utf-8")
    # Execute only parameter and identity validation, before cache verification,
    # socket binding, or process launch. Tiny artifacts are never model-loaded.
    prefix = LAUNCHER.read_text(encoding="utf-8").split("$verificationScript =", 1)[0]
    check = tmp_path / "check.ps1"
    check.write_text(prefix + '\n@{Size=$Size; Name=$provenanceName; Path=$checkpointPath; Log=$logPrefix} | ConvertTo-Json\n', encoding="utf-8")
    arguments = [] if size == "4b" else ["-Size", size]
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(check),
                             "-Directory", str(local), *arguments], capture_output=True, text=True, timeout=20)
    if corrupt:
        assert result.returncode != 0
        assert "Pinned Kev" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        assert value["Size"] == size
        assert Path(value["Path"]) == local / size
        assert value["Log"] == ("kev9" if size == "9b" else "kev")


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")
def test_launcher_preflight_failure_stops_before_port_binding(tmp_path):
    source = LAUNCHER.read_text(encoding="utf-8")
    preflight = source.split("$verificationScript =", 1)[1].split("$portCheck =", 1)[0]
    preflight = "$verificationScript =" + preflight
    # PowerShell aliases resolve an invocation target before external executables.
    # This mocks only the verifier, without a fake executable or socket binding.
    check = tmp_path / "verify-failure.ps1"
    check.write_text("""
$ErrorActionPreference = 'Stop'
$localRoot = 'toy cache'
$Size = '9B'
$pythonPath = 'Invoke-ToyVerifier'
function Invoke-ToyVerifier {
    $script:captured = @($args)
    $global:LASTEXITCODE = 7
}
try {
""" + preflight + """
    throw 'Preflight incorrectly allowed startup'
} catch {
    @{Message=$_.Exception.Message; Arguments=$script:captured} | ConvertTo-Json
}
""", encoding="utf-8")
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(check)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["Message"] == "Offline Kev verification failed. No server was started."
    assert value["Arguments"][1:] == ["--verify-only", "--directory", "toy cache", "--size", "9b"]


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")
def test_launcher_profile_blocks_inherited_cache_and_model_overrides(tmp_path):
    source = LAUNCHER.read_text(encoding="utf-8")
    environment_block = "$environment =" + source.split("$environment =", 1)[1].split("$priorEnvironment =", 1)[0]
    check = tmp_path / "profile.ps1"
    local = tmp_path / "toy cache"
    quoted_local = str(local).replace("'", "''")
    check.write_text(f"$localRoot = '{quoted_local}'\n" + environment_block + "\n$environment | ConvertTo-Json\n", encoding="utf-8")
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(check)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert Path(value["HF_HUB_CACHE"]) == Path(value["HUGGINGFACE_HUB_CACHE"]) == local / "models/huggingface/hub"
    assert value["TRANSFORMERS_CACHE"] is None
    assert value["KEV_LORA_SCALE"] == "1"
    assert value["KEV_MERGE"] == "1"
    assert value["KEV_TEMPERATURE"] is None
    assert value["KEV_PREFIX_MIN_TOKENS"] is None
    assert value["HF_HUB_OFFLINE"] == value["TRANSFORMERS_OFFLINE"] == "1"
