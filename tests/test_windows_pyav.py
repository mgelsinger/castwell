"""Offline source-wheel safeguards without compilers or model execution."""
import base64
import copy
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("windows_pyav_build", ROOT / "scripts/build_windows_pyav.py")
pyav = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pyav)


def test_lock_pins_source_tools_and_version_only_patch():
    lock = json.loads((ROOT / "packaging/windows-pyav.lock.json").read_text())
    pyav.validate_lock(lock)
    assert lock["source"]["sha256"] == "a094b4fd87a3721dacf02794d3d2c82b8d712c85b9534437e82a8a978c175ffd"
    assert lock["msvc_tools_version"] == "14.44.35207"
    changed = copy.deepcopy(lock)
    changed["version_patch"]["after"] += "print('unexpected change')\n"
    with pytest.raises(ValueError, match="version-only patch"):
        pyav.validate_lock(changed)
    changed = copy.deepcopy(lock)
    changed["build_tools"][0]["filename"] = "../escape.whl"
    with pytest.raises(ValueError, match="plain filenames"):
        pyav.validate_lock(changed)


def test_build_environment_does_not_inherit_runtime_or_compiler_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "private-unrelated-runtime-directory")
    monkeypatch.setenv("PYTHONPATH", "private-python-path")
    monkeypatch.setenv("CL", "injected-compiler-options")
    monkeypatch.setenv("CASTWELL_AI_KEY", "private-key")
    monkeypatch.setenv("SystemRoot", "C:/Windows")
    env = pyav.sanitized_environment(tmp_path)
    assert env["PATH"] == str(Path("C:/Windows/System32"))
    assert not {"PYTHONPATH", "CL", "CASTWELL_AI_KEY"} & env.keys()
    assert env["PYTHONNOUSERSITE"] == "1"


def test_patch_refuses_unreviewed_source_bytes(tmp_path):
    source = tmp_path / "source"
    (source / "av").mkdir(parents=True)
    target = source / "av/about.py"
    target.write_bytes(b'__version__ = "16.1.0"\n')
    patch = json.loads((ROOT / "packaging/windows-pyav.lock.json").read_text())["version_patch"]
    pyav.patch_version(source, patch, tmp_path)
    assert target.read_bytes() == b'__version__ = "16.1.0+castwell1"\n'
    assert pyav.sha256(tmp_path / "version.patch") == patch["sha256"]
    with pytest.raises(ValueError, match="reviewed source"):
        pyav.patch_version(source, patch, tmp_path)


def msvc_fixture(root):
    compiler = root / "VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64/cl.exe"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"compiler")
    vcvars = root / "VC/Auxiliary/Build/vcvars64.bat"
    vcvars.parent.mkdir(parents=True)
    vcvars.write_bytes(b"compiler setup")


def test_msvc_discovery_honors_explicit_install_without_edition_assumptions(tmp_path, monkeypatch):
    enterprise = tmp_path / "Visual Studio/2022/Enterprise"
    msvc_fixture(enterprise)
    monkeypatch.setenv("VSINSTALLDIR", str(enterprise))
    assert pyav.find_msvc_install("14.44.35207", {"ProgramFiles(x86)": str(tmp_path / "no-vswhere")}) == enterprise
    with pytest.raises(ValueError, match="Pinned MSVC"):
        pyav.find_msvc_install("14.44.99999", {"ProgramFiles(x86)": str(tmp_path / "no-vswhere")})


def test_msvc_discovery_checks_all_editions_for_pinned_tools(tmp_path, monkeypatch):
    monkeypatch.delenv("VSINSTALLDIR", raising=False)
    where = tmp_path / "Microsoft Visual Studio/Installer/vswhere.exe"
    where.parent.mkdir(parents=True)
    where.write_bytes(b"vswhere")
    enterprise = tmp_path / "Visual Studio/2022/Enterprise"
    msvc_fixture(enterprise)
    def installed(command, **kwargs):
        assert "-all" in command and "-latest" not in command
        return SimpleNamespace(stdout=str(tmp_path / "NewerVSWithoutPinnedTools") + "\n" + str(enterprise) + "\n")
    monkeypatch.setattr(pyav.subprocess, "run", installed)
    assert pyav.find_msvc_install("14.44.35207", {"ProgramFiles(x86)": str(tmp_path)}) == enterprise


def media_fixture(tmp_path):
    prefix = tmp_path / "shared-prefix"
    files = []
    development = []
    for name, major in pyav.LIBRARIES.items():
        for relative in (f"bin/{name}-{major}.dll", f"lib/{name}.lib", f"include/lib{name}/version.h"):
            path = prefix / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode())
            row = pyav.record(path, tmp_path)
            if relative.startswith("bin/"):
                files.append(dict(kind="shared", **row))
            else:
                development.append(row)
    lock, recipe = tmp_path / "media.lock", tmp_path / "media.py"
    lock.write_bytes(b"media lock")
    recipe.write_bytes(b"media recipe")
    manifest = {"schema_version": 1, "profile": "castwell-audio1", "shared_prefix": str(prefix),
                "lock_sha256": pyav.sha256(lock), "recipe_sha256": pyav.sha256(recipe),
                "files": files, "shared_development_files": development}
    path = tmp_path / "build-manifest.json"
    path.write_text(json.dumps(manifest))
    return prefix, path, lock, recipe


def test_media_requires_matching_recipe_and_native_inputs(tmp_path):
    prefix, manifest, lock, recipe = media_fixture(tmp_path)
    result, files = pyav.verified_media(prefix, manifest, lock, recipe)
    assert len(files) == 21
    (prefix / "include/libavcodec/version.h").write_bytes(b"unreviewed header")
    with pytest.raises(ValueError, match="build input differs"):
        pyav.verified_media(prefix, manifest, lock, recipe)
    recipe.write_bytes(b"different recipe")
    with pytest.raises(ValueError, match="pinned lock and recipe"):
        pyav.verified_media(prefix, manifest, lock, recipe)


def wheel_fixture(tmp_path, extra=None):
    wheel = tmp_path / "av-16.1.0+castwell1-cp312-cp312-win_amd64.whl"
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    with zipfile.ZipFile(wheel, "w") as archive:
        info = "av-16.1.0+castwell1.dist-info"
        archive.writestr(info + "/METADATA", "Name: av\nVersion: 16.1.0+castwell1\n")
        archive.writestr(info + "/licenses/LICENSE.txt", "PyAV license")
        for name, major in pyav.LIBRARIES.items():
            original = f"{name}-{major}.dll"
            (prefix / "bin" / original).write_bytes(original.encode())
            archive.writestr(f"av.libs/{name}-{major}-" + "a" * 32 + ".dll", b"mangled")
        if extra:
            archive.writestr("av.libs/" + extra, "uncontrolled runtime")
    return wheel, prefix


def test_wheel_accepts_only_controlled_shared_ffmpeg_dependencies(tmp_path):
    wheel, prefix = wheel_fixture(tmp_path)
    result = pyav.inspect_wheel(wheel, "16.1.0+castwell1", prefix)
    assert len(result["bundled_dlls"]) == 7
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("av.libs/libx264-unsafe.dll", "unexpected")
    with pytest.raises(ValueError, match="Unexpected bundled native dependency"):
        pyav.inspect_wheel(wheel, "16.1.0+castwell1", prefix)


def test_notices_are_verbatim_and_wheel_record_matches_every_member(tmp_path):
    wheel, prefix = wheel_fixture(tmp_path)
    rows = []
    for index in range(6):
        path = tmp_path / f"source{index}/COPYING"
        path.parent.mkdir()
        path.write_bytes(("verbatim source notice " + str(index)).encode())
        rows.append(pyav.record(path, tmp_path))
    media = {"profile": "castwell-audio1", "lock_sha256": "a" * 64, "recipe_sha256": "b" * 64,
             "source_license_files": rows, "archives": []}
    notices = pyav.add_media_notices(wheel, media, tmp_path)
    assert len(notices) == 6
    with zipfile.ZipFile(wheel) as archive:
        record_name = next(name for name in archive.namelist() if name.endswith(".dist-info/RECORD"))
        entries = list(csv.reader(io.StringIO(archive.read(record_name).decode())))
        assert {row[0] for row in entries} == set(archive.namelist())
        for name, expected, size in entries:
            if name == record_name:
                assert not expected and not size
                continue
            data = archive.read(name)
            assert expected == "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            assert size == str(len(data))
        for row in notices:
            assert hashlib.sha256(archive.read(row["relative_path"])).hexdigest() == row["sha256"]


def test_offline_cache_requires_exact_locked_size_and_hash(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    source = cache / "source.tar.gz"
    source.write_bytes(b"source")
    item = {"filename": source.name, "sha256": pyav.sha256(source), "bytes": 6, "url": "https://example.invalid/source"}
    assert pyav.fetch(item, tmp_path / "copied", cache, True).read_bytes() == b"source"
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Cached input checksum or size"):
        pyav.fetch(item, tmp_path / "other", cache, True)
