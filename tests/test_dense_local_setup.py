"""Offline setup and launcher checks using tiny artifacts, never model inference."""
from __future__ import annotations

import ast
import base64
import ctypes
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import requests


ROOT = Path(__file__).resolve().parents[1]
SETUP_PATH = ROOT / "scripts/setup_qwen35_dense_local.py"
LAUNCHER = ROOT / "scripts/start_qwen35_dense_local.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
ARTIFACT = b"GGUF tiny offline setup test artifact\n"


@pytest.fixture(params=["qwen35", "qwen38"])
def setup(monkeypatch, request):
    path = ROOT / f"scripts/setup_{request.param}_dense_local.py"
    spec = importlib.util.spec_from_file_location("dense_setup_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "MODEL_SIZE", len(ARTIFACT))
    monkeypatch.setattr(module, "MODEL_SHA256", hashlib.sha256(ARTIFACT).hexdigest())
    return module


def fake_download(monkeypatch, chunks, *, length=None):
    class Response:
        status_code = 200
        headers = {} if length is None else {"Content-Length": str(length)}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, **kwargs):
            for item in chunks:
                if isinstance(item, Exception):
                    raise item
                yield item

    class Session:
        def __init__(self):
            self.headers = {}
            self.trust_env = True
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    session = Session()
    monkeypatch.setattr(requests, "Session", lambda: session)
    return session


def forbid_download(monkeypatch, setup):
    def forbidden(*args, **kwargs):
        raise AssertionError("No download is allowed in this check")
    monkeypatch.setattr(setup, "download", forbidden)


def test_setup_source_is_python310_syntax():
    ast.parse(SETUP_PATH.read_text(encoding="utf-8"), feature_version=(3, 10))


def test_download_is_pinned_credential_free_and_published_only_after_full_hash(setup, tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "unused-private-test-token")
    monkeypatch.setenv("HTTPS_PROXY", "http://unused-proxy.invalid")
    session = fake_download(monkeypatch, [ARTIFACT[:8], ARTIFACT[8:]], length=len(ARTIFACT))
    result = setup.prepare(tmp_path)
    model = tmp_path / setup.MODEL_NAME
    assert model.read_bytes() == ARTIFACT
    assert not model.with_suffix(model.suffix + ".partial").exists()
    assert not session.trust_env
    assert "Authorization" not in session.headers
    assert session.calls == [(setup.model_url(), {"stream": True, "timeout": (30, 120)})]
    assert result["sha256"] == hashlib.sha256(ARTIFACT).hexdigest()
    saved = json.loads((tmp_path / setup.PROVENANCE_NAME).read_text(encoding="utf-8"))
    assert saved == result
    assert "unused-private-test-token" not in json.dumps(saved)


@pytest.mark.parametrize("corrupt", [b"short", b"x" * len(ARTIFACT)])
def test_corrupt_existing_model_is_never_replaced(setup, tmp_path, monkeypatch, corrupt):
    model = tmp_path / setup.MODEL_NAME
    model.write_bytes(corrupt)
    forbid_download(monkeypatch, setup)
    with pytest.raises(ValueError, match="wrong size|SHA256 mismatch"):
        setup.prepare(tmp_path)
    assert model.read_bytes() == corrupt
    assert not (tmp_path / setup.PROVENANCE_NAME).exists()


def test_interrupted_existing_download_is_preserved_without_network(setup, tmp_path, monkeypatch):
    partial = tmp_path / (setup.MODEL_NAME + ".partial")
    partial.write_bytes(ARTIFACT[:7])
    forbid_download(monkeypatch, setup)
    with pytest.raises(ValueError, match="Interrupted download exists"):
        setup.prepare(tmp_path)
    assert partial.read_bytes() == ARTIFACT[:7]
    assert not (tmp_path / setup.MODEL_NAME).exists()


@pytest.mark.parametrize("chunks", [[ARTIFACT[:7]], [b"x" * len(ARTIFACT)],
                                    [ARTIFACT[:7], requests.ConnectionError("connection interrupted")]])
def test_failed_transfers_never_publish_and_retain_partial(setup, tmp_path, monkeypatch, chunks):
    fake_download(monkeypatch, chunks)
    with pytest.raises(ValueError):
        setup.prepare(tmp_path)
    assert not (tmp_path / setup.MODEL_NAME).exists()
    assert (tmp_path / (setup.MODEL_NAME + ".partial")).read_bytes() == b"".join(
        item for item in chunks if isinstance(item, bytes))
    assert not (tmp_path / setup.PROVENANCE_NAME).exists()


def test_oversized_response_is_rejected_before_publishing(setup, tmp_path, monkeypatch):
    fake_download(monkeypatch, [ARTIFACT, b"unexpected extra bytes"])
    with pytest.raises(ValueError, match="exceeds the pinned size"):
        setup.prepare(tmp_path)
    assert not (tmp_path / setup.MODEL_NAME).exists()
    assert (tmp_path / (setup.MODEL_NAME + ".partial")).read_bytes() == ARTIFACT


def test_verify_only_checks_existing_bytes_without_network_or_file_changes(setup, tmp_path, monkeypatch):
    model = tmp_path / setup.MODEL_NAME
    model.write_bytes(ARTIFACT)
    forbid_download(monkeypatch, setup)
    original = model.stat().st_mtime_ns
    assert setup.prepare(tmp_path, verify_only=True)["size_bytes"] == len(ARTIFACT)
    assert list(tmp_path.iterdir()) == [model]
    assert model.stat().st_mtime_ns == original


def test_verify_only_missing_file_never_downloads_or_creates_directory(setup, tmp_path, monkeypatch):
    forbid_download(monkeypatch, setup)
    directory = tmp_path / "does not exist"
    with pytest.raises(ValueError, match="verify-only never downloads"):
        setup.prepare(directory, verify_only=True)
    assert not directory.exists()


def test_destination_race_preserves_both_files(setup, tmp_path, monkeypatch):
    original_verify = setup.verify_model
    model = tmp_path / setup.MODEL_NAME
    def collision(path):
        original_verify(path)
        model.write_bytes(b"another process owns this file")
    monkeypatch.setattr(setup, "verify_model", collision)
    fake_download(monkeypatch, [ARTIFACT])
    with pytest.raises(ValueError, match="destination appeared"):
        setup.prepare(tmp_path)
    assert model.read_bytes() == b"another process owns this file"
    assert (tmp_path / (setup.MODEL_NAME + ".partial")).read_bytes() == ARTIFACT


def test_existing_provenance_mismatch_is_not_rewritten(setup, tmp_path, monkeypatch):
    (tmp_path / setup.MODEL_NAME).write_bytes(ARTIFACT)
    provenance = tmp_path / setup.PROVENANCE_NAME
    before = json.dumps(dict(setup.identity(tmp_path / setup.MODEL_NAME), revision="different"))
    provenance.write_text(before, encoding="utf-8")
    forbid_download(monkeypatch, setup)
    with pytest.raises(ValueError, match="provenance does not match"):
        setup.prepare(tmp_path)
    assert provenance.read_text(encoding="utf-8") == before


def powershell(code):
    encoded = base64.b64encode(code.encode("utf-16-le")).decode("ascii")
    return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
                          capture_output=True, text=True, timeout=30)


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")
def test_launcher_parses_without_executing_it():
    path = str(LAUNCHER).replace("'", "''")
    result = powershell(f"$e=$null; $t=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$t,[ref]$e); if($e.Count){{$e | Out-String | Write-Error; exit 1}}")
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")
@pytest.mark.parametrize("extra,mode,budget", [("", "on", "1024"), ("-NoThinking -ReasoningBudget 7000", "off", "0")])
def test_launcher_validation_resolves_dense_profile_without_starting_process(tmp_path, extra, mode, budget):
    model = tmp_path / "model with spaces.gguf"
    binary = tmp_path / "server with spaces.exe"
    model.write_bytes(b"mock model")
    binary.write_bytes(b"mock runtime")
    quoted = lambda value: "'" + str(value).replace("'", "''") + "'"
    result = powershell(f"""
$ErrorActionPreference='Stop'
function Get-Item {{ param($LiteralPath) [pscustomobject]@{{Length=16740812704}} }}
function Get-FileHash {{ param($LiteralPath,$Algorithm)
    $hash=if($LiteralPath -eq {quoted(model)}){{'84b5f7f112156d63836a01a69dc3f11a6ba63b10a23b8ca7a7efaf52d5a2d806'}}else{{'7b886298b688509ced3e92b420edd57dd3d665da72c1fc207d7a537be5870352'}}
    [pscustomobject]@{{Hash=$hash}}
}}
function Start-Process {{ throw 'The validation check must not launch anything' }}
& {quoted(LAUNCHER)} -Model {quoted(model)} -ServerBinary {quoted(binary)} -ValidateOnly {extra} | ConvertTo-Json -Depth 5 -Compress
""")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    arguments = data["Arguments"]
    assert data["StartsServer"] is False
    assert arguments[arguments.index("--model") + 1] == str(model)
    assert arguments[arguments.index("--reasoning") + 1] == mode
    assert arguments[arguments.index("--reasoning-budget") + 1] == budget
    assert arguments[arguments.index("--ctx-size") + 1] == "8192"
    assert arguments[arguments.index("--n-gpu-layers") + 1] == "all"
    assert arguments[arguments.index("--host") + 1] == "127.0.0.1"
    assert "--offline" in arguments and "--n-cpu-moe" not in arguments


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")
@pytest.mark.parametrize("bad_file,expected", [("model", "pinned Unsloth"), ("binary", "b11146 server entrypoint")])
def test_launcher_rejects_changed_model_or_server_before_launch(tmp_path, bad_file, expected):
    model = tmp_path / "model.gguf"
    binary = tmp_path / "server.exe"
    model.write_bytes(b"mock model")
    binary.write_bytes(b"mock runtime")
    quoted = lambda value: "'" + str(value).replace("'", "''") + "'"
    model_hash = "0" * 64 if bad_file == "model" else "84b5f7f112156d63836a01a69dc3f11a6ba63b10a23b8ca7a7efaf52d5a2d806"
    binary_hash = "0" * 64 if bad_file == "binary" else "7b886298b688509ced3e92b420edd57dd3d665da72c1fc207d7a537be5870352"
    result = powershell(f"""
$ErrorActionPreference='Stop'
function Get-Item {{ param($LiteralPath) [pscustomobject]@{{Length=16740812704}} }}
function Get-FileHash {{ param($LiteralPath,$Algorithm)
    $hash=if($LiteralPath -eq {quoted(model)}){{'{model_hash}'}}else{{'{binary_hash}'}}
    [pscustomobject]@{{Hash=$hash}}
}}
function Start-Process {{ throw 'No launch is allowed' }}
& {quoted(LAUNCHER)} -Model {quoted(model)} -ServerBinary {quoted(binary)} -ValidateOnly
""")
    assert result.returncode != 0
    assert expected in result.stderr
    assert "No launch is allowed" not in result.stderr


@pytest.mark.skipif(not POWERSHELL or sys.platform != "win32", reason="Windows native argument parsing is required")
def test_background_native_quoting_roundtrips_spaces_quotes_and_trailing_backslashes():
    values = [r"D:\Models with spaces\model.gguf", 'a "quoted" value', "", "D:\\directory with spaces\\", "ordinary"]
    encoded = base64.b64encode(json.dumps(values).encode("utf-8")).decode("ascii")
    path = str(LAUNCHER).replace("'", "''")
    result = powershell(f"""
$ErrorActionPreference='Stop'; $e=$null; $t=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$t,[ref]$e)
$node=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'ConvertTo-NativeArgument'}},$true)
. ([scriptblock]::Create($node.Extent.Text))
$values=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')) | ConvertFrom-Json
@($values | ForEach-Object {{ ConvertTo-NativeArgument $_ }}) | ConvertTo-Json -Compress
""")
    assert result.returncode == 0, result.stderr
    argument_line = "stub.exe " + " ".join(json.loads(result.stdout))
    parse = ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int()
    parsed = parse(argument_line, ctypes.byref(count))
    try:
        assert list(parsed[:count.value])[1:] == values
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(parsed)
