"""Build a source-pinned PyAV wheel against Castwell's controlled shared FFmpeg.

The output receipt binds this recipe, its source lock, the media build, and the
actual wheel produced. It does not assert byte-identical builds across hosts.
Existing application environments and distribution directories are not changed.
"""
from __future__ import annotations

import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LIBRARIES = {"avcodec": 62, "avdevice": 62, "avfilter": 11, "avformat": 62,
             "avutil": 60, "swresample": 6, "swscale": 9}


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def record(path: Path, root: Path) -> dict:
    return {"relative_path": path.relative_to(root).as_posix(),
            "sha256": sha256(path), "bytes": path.stat().st_size}


def plain_filename(value: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value or any(c in value for c in "/\\:"):
        raise ValueError("Input names must be plain filenames")
    return value


def checked_digest(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Invalid SHA256 digest")
    return value


def validate_lock(lock: dict) -> None:
    if (lock.get("schema_version") != 1 or lock.get("name") != "av"
            or lock.get("version") != "16.1.0+castwell1"
            or lock.get("python_tag") != "cp311" or lock.get("abi_tag") != "abi3"
            or lock.get("build_python_tag") != "cp312" or lock.get("platform") != "win_amd64"):
        raise ValueError("Unsupported PyAV source lock")
    names = set()
    for item in [lock["source"], *lock["build_tools"]]:
        name = plain_filename(item["filename"])
        checked_digest(item["sha256"])
        if name in names or not item["url"].startswith("https://") or item["bytes"] <= 0:
            raise ValueError("Invalid or duplicate locked input")
        names.add(name)
    patch = lock["version_patch"]
    if (patch.get("relative_path") != "av/about.py" or patch.get("before") != '__version__ = "16.1.0"\n'
            or patch.get("after") != '__version__ = "16.1.0+castwell1"\n'
            or hashlib.sha256(patch["unified_diff"].encode()).hexdigest() != patch["sha256"]):
        raise ValueError("Invalid version-only patch")
    if not re.fullmatch(r"14\.44\.[0-9]+", lock["msvc_tools_version"]):
        raise ValueError("The MSVC 14.44 toolset must be pinned")


def fetch(item: dict, destination: Path, cache: Path | None, offline: bool) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / plain_filename(item["filename"])
    if cache:
        for candidate in (cache / path.name, cache / "upstream" / path.name,
                          cache / "build/tool-wheelhouse" / path.name):
            if candidate.is_file():
                if sha256(candidate) != item["sha256"] or candidate.stat().st_size != item["bytes"]:
                    raise ValueError("Cached input checksum or size differs: " + path.name)
                shutil.copyfile(candidate, path)
                break
    if not path.exists():
        if offline:
            raise ValueError("Offline input is missing: " + path.name)
        context = ssl.create_default_context()
        try:
            import certifi
            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
        request = urllib.request.Request(item["url"], headers={"User-Agent": "Castwell-PyAV-source-build/1"})
        with urllib.request.urlopen(request, timeout=90, context=context) as source, path.open("wb") as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
    if sha256(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
        raise ValueError("Input checksum or size differs: " + path.name)
    return path


def verified_media(prefix: Path, manifest_path: Path, media_lock: Path, media_recipe: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != 1 or manifest.get("profile") != "castwell-audio1"
            or manifest.get("lock_sha256") != sha256(media_lock)
            or manifest.get("recipe_sha256") != sha256(media_recipe)):
        raise ValueError("Shared media build does not match its pinned lock and recipe")
    if Path(manifest.get("shared_prefix", "")).resolve() != prefix.resolve():
        raise ValueError("Shared media prefix differs from its build receipt")
    rows = {item["relative_path"]: item for item in manifest["files"] if item.get("kind") == "shared"}
    files = []
    for path in sorted(prefix.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(prefix).as_posix()
        if relative.startswith(("include/", "lib/")) or (relative.startswith("bin/") and path.suffix == ".dll"):
            files.append(record(path, prefix))
    for name, major in LIBRARIES.items():
        path = prefix / "bin" / f"{name}-{major}.dll"
        key = path.relative_to(manifest_path.parent).as_posix()
        expected = rows.get(key)
        if not expected or not path.is_file() or sha256(path) != expected["sha256"] or path.stat().st_size != expected["bytes"]:
            raise ValueError("Shared media DLL differs or is missing: " + path.name)
        if not (prefix / "lib" / (name + ".lib")).is_file():
            raise ValueError("MSVC import library is missing: " + name)
        if not (prefix / "include" / ("lib" + name)).is_dir():
            raise ValueError("FFmpeg public headers are missing: " + name)
    # If supplied by the media recipe, enforce its header and import-library hashes too.
    for item in manifest.get("shared_development_files", []):
        path = manifest_path.parent / item["relative_path"]
        if not path.resolve().is_relative_to(prefix.resolve()) or not path.is_file() or sha256(path) != item["sha256"]:
            raise ValueError("Shared media build input differs from its receipt")
    return manifest, files


def sanitized_environment(output: Path) -> dict[str, str]:
    # Do not inherit PATH, compiler flags, Python configuration or model/API settings.
    allowed = ("SystemRoot", "SystemDrive", "ComSpec", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    system = Path(env.get("SystemRoot", "C:/Windows"))
    env.update(PATH=str(system / "System32"), TEMP=str(output / "tmp"), TMP=str(output / "tmp"),
               PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1", PIP_CONFIG_FILE=os.devnull,
               PYTHONNOUSERSITE="1", SOURCE_DATE_EPOCH="1786406400")
    return env


def find_msvc_install(version: str, env: dict[str, str]) -> Path:
    """Find the pinned tools in any VS edition, including CI's Enterprise."""
    candidates = []
    configured = os.environ.get("VSINSTALLDIR")
    if configured:
        candidates.append(Path(configured))
    vswhere = Path(env.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
    if vswhere.is_file():
        found = subprocess.run([str(vswhere), "-all", "-products", "*", "-requires",
                                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                               env=env, capture_output=True, text=True, check=True, timeout=30).stdout
        candidates.extend(Path(line.strip()) for line in found.splitlines() if line.strip())
    for base in candidates:
        if ((base / "VC/Tools/MSVC" / version / "bin/Hostx64/x64/cl.exe").is_file()
                and (base / "VC/Auxiliary/Build/vcvars64.bat").is_file()):
            return base
    raise ValueError("Pinned MSVC 14.44 tools are not installed in any discovered Visual Studio edition")


def msvc_environment(output: Path, version: str) -> tuple[dict[str, str], dict]:
    env = sanitized_environment(output)
    base = find_msvc_install(version, env)
    compiler = base / "VC/Tools/MSVC" / version / "bin/Hostx64/x64/cl.exe"
    vcvars = base / "VC/Auxiliary/Build/vcvars64.bat"
    if not compiler.is_file() or any(c in str(vcvars) for c in '\"%&|<>^'):
        raise ValueError("Pinned MSVC 14.44 tools are not installed")
    batch = output / "setup-msvc.cmd"
    if any(c in str(batch) for c in '\"%&|<>^'):
        raise ValueError("Compiler setup paths cannot contain command-shell metacharacters")
    batch.write_text('@call "' + str(vcvars) + '" -vcvars_ver=' + version
                     + ' >nul\n@if errorlevel 1 exit /b 1\n@set\n', encoding="utf-8")
    result = subprocess.run([env.get("ComSpec", str(Path(env["SystemRoot"]) / "System32/cmd.exe")),
                             "/d", "/c", str(batch)], env=env, capture_output=True,
                            text=True, check=True, timeout=60)
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key and not key.startswith("="):
            env[key] = value
    if env.get("VCToolsVersion", "").rstrip("\\/") != version:
        raise ValueError("Compiler environment did not select the pinned MSVC toolset")
    env.update(DISTUTILS_USE_SDK="1", MSSdk="1")
    version_result = subprocess.run([str(compiler)], env=env, capture_output=True, text=True, timeout=30)
    lines = (version_result.stderr + version_result.stdout).splitlines()
    banner = next((line for line in lines if "Compiler Version" in line), lines[0] if lines else "")
    return env, {"tools_version": version, "compiler_sha256": sha256(compiler), "compiler_banner": banner}


def patch_version(source: Path, patch: dict, output: Path) -> None:
    target = source / patch["relative_path"]
    if target.read_bytes() != patch["before"].encode():
        raise ValueError("PyAV version file differs from the reviewed source")
    target.write_bytes(patch["after"].encode())
    (output / "version.patch").write_bytes(patch["unified_diff"].encode())


def add_media_notices(wheel: Path, media: dict, media_root: Path) -> list[dict]:
    """Retain verbatim source notices and update the wheel's integrity RECORD."""
    rows = media.get("source_license_files", [])
    if len(rows) < 6:
        raise ValueError("Controlled media source notices are missing")
    with zipfile.ZipFile(wheel) as archive:
        content = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    metadata = next(name for name in content if name.endswith(".dist-info/METADATA"))
    info = metadata.rsplit("/", 1)[0]
    notices = []
    for index, row in enumerate(rows, 1):
        source = media_root / row["relative_path"]
        if (not source.resolve().is_relative_to(media_root.resolve()) or not source.is_file()
                or sha256(source) != row["sha256"] or source.stat().st_size != row["bytes"]):
            raise ValueError("Media source notice differs from its receipt")
        target = info + "/licenses/castwell-media/" + str(index) + "-" + source.name
        content[target] = source.read_bytes()
        notices.append({"relative_path": target, "source_relative_path": row["relative_path"],
                        "sha256": row["sha256"], "bytes": row["bytes"]})
    content[info + "/licenses/castwell-media/SOURCES.json"] = (json.dumps({
        "media_profile": media["profile"], "media_lock_sha256": media["lock_sha256"],
        "media_recipe_sha256": media["recipe_sha256"], "notices": notices,
        "source_archives": media["archives"]}, indent=2) + "\n").encode()
    record_name = info + "/RECORD"
    content.pop(record_name, None)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    for name, data in sorted(content.items()):
        value = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow((name, "sha256=" + value, len(data)))
    writer.writerow((record_name, "", ""))
    content[record_name] = buffer.getvalue().encode()
    temporary = wheel.with_suffix(".notices.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(content.items()):
            entry = zipfile.ZipInfo(name, date_time=(2026, 8, 11, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    temporary.replace(wheel)
    return notices


def inspect_wheel(wheel: Path, version: str, prefix: Path) -> dict:
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        parsed = BytesParser().parsebytes(archive.read(metadata))
        if parsed["Name"] != "av" or parsed["Version"] != version:
            raise ValueError("Built wheel metadata differs from the source lock")
        dlls = []
        for item in archive.infolist():
            if not item.filename.lower().endswith(".dll"):
                continue
            name = Path(item.filename).name
            match = re.fullmatch(r"([a-z]+)-(\d+)-[a-f0-9]+\.dll", name)
            if not match or LIBRARIES.get(match[1]) != int(match[2]):
                raise ValueError("Unexpected bundled native dependency: " + name)
            origin = prefix / "bin" / f"{match[1]}-{match[2]}.dll"
            dlls.append({"relative_path": item.filename, "sha256": hashlib.sha256(archive.read(item)).hexdigest(),
                         "bytes": item.file_size, "source_filename": origin.name, "source_sha256": sha256(origin),
                         "transformation": "delvewheel dependency-name mangling"})
        if {item["source_filename"] for item in dlls} != {f"{name}-{major}.dll" for name, major in LIBRARIES.items()}:
            raise ValueError("The repaired wheel must contain exactly seven controlled FFmpeg libraries")
        if not any("license" in name.lower() for name in archive.namelist()):
            raise ValueError("PyAV license is missing from the wheel")
    return {"name": parsed["Name"], "version": parsed["Version"], "bundled_dlls": dlls}


def build(args) -> dict:
    lock_path, media_lock, media_recipe = args.lock.resolve(), ROOT / "packaging/windows-media.lock.json", ROOT / "scripts/build_windows_media.py"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    validate_lock(lock)
    output, prefix, media_path = args.output.resolve(), args.ffmpeg_prefix.resolve(), args.media_manifest.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a fresh, empty PyAV output directory")
    if output == ROOT or output in ROOT.parents or output == prefix or output.is_relative_to(prefix) or prefix.is_relative_to(output):
        raise ValueError("Use a dedicated PyAV output directory outside the shared prefix")
    output.mkdir(parents=True, exist_ok=True)
    for folder in ("tmp", "inputs", "logs", "raw-wheel", "wheelhouse", "source"):
        (output / folder).mkdir()
    media, prefix_files = verified_media(prefix, media_path, media_lock, media_recipe)
    identities = {"lock_sha256": sha256(lock_path), "recipe_sha256": sha256(Path(__file__)),
                  "media_manifest_sha256": sha256(media_path), "media_lock_sha256": sha256(media_lock),
                  "media_recipe_sha256": sha256(media_recipe)}
    commands = []

    def run(name, command, cwd, env, timeout=1200):
        print(name, flush=True)
        log = output / "logs" / (name + ".log")
        started = time.monotonic()
        with log.open("wb") as stream:
            result = subprocess.run([str(part) for part in command], cwd=cwd, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=timeout,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        commands.append({"name": name, "argv": [str(part) for part in command], "returncode": result.returncode,
                         "seconds": round(time.monotonic() - started, 3), "log_sha256": sha256(log)})
        (output / "commands.json").write_text(json.dumps(commands, indent=2) + "\n", encoding="utf-8")
        if result.returncode:
            raise RuntimeError(name + " failed; inspect its retained build log")

    cache = args.cache.resolve() if args.cache else None
    source_archive = fetch(lock["source"], output / "inputs", cache, args.offline)
    tools = [fetch(item, output / "inputs", cache, args.offline) for item in lock["build_tools"]]
    env, compiler = msvc_environment(output, lock["msvc_tools_version"])
    venv = output / "venv"
    run("create-build-environment", [sys.executable, "-m", "venv", venv], output, env, timeout=120)
    python = venv / "Scripts/python.exe"
    requirements = output / "build-tools.lock"
    requirements.write_text("".join(item["name"] + "==" + item["version"] + " --hash=sha256:" + item["sha256"] + "\n"
                                   for item in lock["build_tools"]), encoding="utf-8")
    run("install-build-tools", [python, "-m", "pip", "install", "--no-index", "--no-deps", "--require-hashes",
                                "--find-links", output / "inputs", "-r", requirements], output, env, timeout=180)
    with tarfile.open(source_archive) as archive:
        archive.extractall(output / "source", filter="data")
    source = output / "source" / ("av-" + lock["upstream_version"])
    patch_version(source, lock["version_patch"], output)
    run("build-pyav", [python, "setup.py", "--ffmpeg-dir=" + str(prefix), "build_ext", "--parallel=" + str(args.jobs),
                       "bdist_wheel", "--dist-dir=" + str(output / "raw-wheel")], source, env)
    raw = output / "raw-wheel" / lock["wheel_filename"]
    if not raw.is_file():
        raise ValueError("The build did not produce the expected wheel filename")
    run("repair-pyav", [python, "-m", "delvewheel", "repair", "--add-path", prefix / "bin", "-w", output / "wheelhouse", raw], output, env, timeout=180)
    wheel = output / "wheelhouse" / lock["wheel_filename"]
    notices = add_media_notices(wheel, media, media_path.parent)
    details = inspect_wheel(wheel, lock["version"], prefix)
    # Prove loading without depending on the source/build directory or media PATH.
    run("install-custom-wheel", [python, "-m", "pip", "install", "--no-index", "--no-deps", wheel], output, env, timeout=120)
    run("import-custom-wheel", [python, "-I", "-c", "import av,json;print(json.dumps({'version':av.__version__,'libraries':av.library_versions}))"],
        output, sanitized_environment(output), timeout=30)
    final_media, final_files = verified_media(prefix, media_path, media_lock, media_recipe)
    if final_files != prefix_files or {key: sha256(path) for key, path in (
            ("lock_sha256", lock_path), ("recipe_sha256", Path(__file__)), ("media_manifest_sha256", media_path),
            ("media_lock_sha256", media_lock), ("media_recipe_sha256", media_recipe))} != identities:
        raise ValueError("Source or shared-media inputs changed during the build")
    manifest = {"schema_version": 1, "name": "av", "version": lock["version"], **identities,
                "wheel": {"filename": wheel.name, **record(wheel, output)}, "raw_wheel": record(raw, output),
                "source": record(source_archive, output), "version_patch": record(output / "version.patch", output),
                "build_tools": [dict(name=item["name"], version=item["version"], **record(path, output))
                                for item, path in zip(lock["build_tools"], tools)],
                "ffmpeg_prefix_files": prefix_files, "compiler": compiler, **details,
                "source_notices": notices,
                "media_profile": media["profile"], "source_modified": "version metadata only",
                "byte_reproducibility_verified": False, "build_commands": commands,
                "validation": {"isolated_import": "passed", "audio_decode": "not_run_by_builder"}}
    (output / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg-prefix", type=Path, required=True)
    parser.add_argument("--media-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lock", type=Path, default=ROOT / "packaging/windows-pyav.lock.json")
    parser.add_argument("--cache", type=Path, help="Optional directory containing already verified source and tool archives")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if os.name != "nt" or platform.machine().lower() not in ("amd64", "x86_64") or sys.version_info[:2] != (3, 12):
        parser.error("A Windows x64 CPython 3.12 build host is required")
    if not 1 <= args.jobs <= 16:
        parser.error("Use between 1 and 16 build jobs")
    print(json.dumps(build(args), indent=2))


if __name__ == "__main__":
    main()
