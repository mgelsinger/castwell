"""Build pinned Windows audio tools and optional shared FFmpeg libraries.

Uses a private portable toolchain, retains source archives and object files, and
never installs tools globally or replaces Castwell's existing packaged files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LIBRARIES = ("avcodec", "avdevice", "avfilter", "avformat", "avutil", "swresample", "swscale")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_archive_name(value: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value or "/" in value or "\\" in value:
        raise ValueError("Archive filenames must be plain filenames")
    return value


def fetch(item: dict, directory: Path, *, offline: bool) -> Path:
    target = directory / checked_archive_name(item["filename"])
    expected = item["sha256"]
    if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("Invalid archive checksum")
    if target.exists():
        if sha256(target) != expected:
            raise ValueError(f"Existing archive checksum differs: {target.name}")
        return target
    if offline:
        raise ValueError(f"Required offline archive is missing: {target.name}")
    if not item["url"].startswith("https://"):
        raise ValueError("Archive downloads require HTTPS")
    context = ssl.create_default_context()
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    partial = target.with_suffix(target.suffix + ".partial")
    request = urllib.request.Request(item["url"], headers={"User-Agent": "Castwell-media-source-build/1"})
    with urllib.request.urlopen(request, timeout=90, context=context) as source, partial.open("wb") as output:
        shutil.copyfileobj(source, output, 1024 * 1024)
    if sha256(partial) != expected or partial.stat().st_size != item["bytes"]:
        raise ValueError(f"Downloaded archive checksum or size differs: {target.name}")
    partial.replace(target)
    return target


def unpack(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                if not (destination / item.filename).resolve().is_relative_to(destination.resolve()):
                    raise ValueError("ZIP member escapes its destination")
            source.extractall(destination)
    else:
        if not hasattr(tarfile, "data_filter"):
            raise RuntimeError("Safe source extraction requires Python 3.12 or a tarfile security backport")
        with tarfile.open(archive) as source:
            source.extractall(destination, filter="data")


def ffmpeg_flags(prefix: Path, *, shared: bool) -> list[str]:
    return ["--prefix=" + prefix.as_posix(), "--target-os=mingw32", "--arch=x86_64", "--cc=gcc", "--cxx=g++",
        "--disable-autodetect", "--disable-gpl", "--disable-nonfree", "--disable-version3",
        "--disable-debug", "--disable-doc", "--disable-network", "--disable-everything",
        "--enable-shared" if shared else "--enable-static", "--disable-static" if shared else "--disable-shared",
        "--enable-ffmpeg", "--enable-ffprobe", "--disable-ffplay",
        "--enable-libmp3lame", "--enable-libvorbis", "--enable-libopus",
        "--enable-protocol=file,pipe",
        "--enable-demuxer=mp3,mov,ogg,wav,flac,aac,ffmetadata",
        "--enable-muxer=mp3,mp4,ipod,wav,flac,ogg,opus,pcm_s16le,pcm_f32le,null,ffmetadata,adts",
        "--enable-decoder=aac,aac_fixed,aac_latm,alac,flac,mp3,mp3float,mp3adu,mp3adufloat,mp3on4,mp3on4float,opus,vorbis,speex,ac3,eac3,pcm_*,adpcm_ima_wav,adpcm_ms",
        "--enable-encoder=libmp3lame,libvorbis,libopus,aac,flac,pcm_s16le,pcm_f32le",
        "--enable-parser=aac,aac_latm,ac3,flac,mpegaudio,opus,vorbis",
        "--enable-filter=abuffer,abuffersink,anull,atrim,asetpts,concat,aresample,aformat,asplit,sine,anullsrc",
        "--enable-indev=lavfi", "--enable-bsf=aac_adtstoasc,extract_extradata",
        "--pkg-config-flags=--static", "--extra-ldflags=" + ("-static-libgcc -static-libstdc++" if shared else "-static"),
        "--extra-cflags=-O2 -fno-ident", "--extra-version=castwell-audio1"]


def msvc_librarian(environ: dict[str, str] | None = None) -> Path:
    """Find x64 import-library tooling across supported Visual Studio editions."""
    env = os.environ if environ is None else environ
    pattern = "VC/Tools/MSVC/*/bin/Hostx64/x64/lib.exe"
    candidates = list(Path(env['VSINSTALLDIR']).glob(pattern)) if env.get('VSINSTALLDIR') else []
    if not candidates:
        roots = {Path(env.get(name, default)) / 'Microsoft Visual Studio'
                 for name, default in (('ProgramFiles(x86)', 'C:/Program Files (x86)'),
                                       ('ProgramFiles', 'C:/Program Files'))}
        candidates = [path for base in roots for path in base.glob('*/*/' + pattern)]
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        raise RuntimeError('Shared PyAV build requires installed MSVC x64 lib.exe to create import libraries')
    return max(candidates, key=lambda path: (tuple(int(value) for value in path.parents[3].name.split('.')),
                                            str(path)))


class Builder:
    def __init__(self, output: Path, lock: dict, lock_path: Path, jobs: int):
        self.output, self.lock, self.lock_path, self.jobs = output, lock, lock_path, jobs
        self.prefix, self.shared = output / "prefix", output / "shared-prefix"
        self.tools = output / "toolchain/w64devkit/bin"
        self.nasm = output / "toolchain/nasm/nasm-2.16.03"
        self.env = dict(os.environ)
        self.env.update(PATH=";".join((self.tools.as_posix(), self.nasm.as_posix(),
            (Path(os.environ["SystemRoot"]) / "System32").as_posix())),
            PKG_CONFIG_PATH=(self.prefix / "lib/pkgconfig").as_posix(),
            CPATH=(self.prefix / "include").as_posix(), LIBRARY_PATH=(self.prefix / "lib").as_posix(),
            SOURCE_DATE_EPOCH=str(lock["source_date_epoch"]), LC_ALL="C", PATH_SEPARATOR=";",
            ac_executable_extensions=".exe", build_alias="x86_64-w64-mingw32",
            TMPDIR=(output / "tmp").as_posix(), TMP=(output / "tmp").as_posix(), TEMP=(output / "tmp").as_posix())
        for name in ("archives", "sources", "build", "logs", "tmp", "prefix", "shared-prefix"):
            (output / name).mkdir(parents=True, exist_ok=True)
        self.commands = []

    def run(self, name: str, command: list[str], cwd: Path) -> None:
        print(name, flush=True)
        started = time.monotonic()
        log = self.output / "logs" / (name + ".log")
        with log.open("wb") as stream:
            result = subprocess.run(command, cwd=cwd, env=self.env, stdout=stream, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
        self.commands.append({"name": name, "argv": command, "cwd": str(cwd),
            "returncode": result.returncode, "seconds": time.monotonic() - started, "log_sha256": sha256(log)})
        with (self.output / "commands.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(self.commands[-1]) + "\n")
        if result.returncode:
            raise RuntimeError(f"{name} failed; inspect {log}")

    def prepare(self, *, offline: bool) -> None:
        self.archives = {item["role"]: fetch(item, self.output / "archives", offline=offline) for item in self.lock["archives"]}
        if not (self.tools / "gcc.exe").exists():
            self.run("extract-toolchain", [str(self.archives["toolchain_binary"]), "-y", "-o" + str(self.output / "toolchain")], self.output)
        if not (self.nasm / "nasm.exe").exists():
            unpack(self.archives["assembler_binary"], self.output / "toolchain/nasm")
        for role in ("ffmpeg_source", "lame_source", "ogg_source", "vorbis_source", "opus_source"):
            unpack(self.archives[role], self.output / "sources")

    def cmake(self, name: str, folder: str, flags: list[str]) -> None:
        target = self.output / "build" / name
        self.run(name + "-configure", [str(self.tools / "cmake.exe"), "-S", (self.output / "sources" / folder).as_posix(),
            "-B", target.as_posix(), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_INSTALL_PREFIX=" + self.prefix.as_posix(), "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
            "-DBUILD_SHARED_LIBS=OFF", *flags], self.output)
        self.run(name + "-build", [str(self.tools / "cmake.exe"), "--build", target.as_posix(), "--parallel", str(self.jobs)], self.output)
        self.run(name + "-install", [str(self.tools / "cmake.exe"), "--install", target.as_posix()], self.output)

    def dependencies(self) -> None:
        self.cmake("ogg", "libogg-1.3.6", ["-DBUILD_TESTING=OFF", "-DINSTALL_DOCS=OFF"])
        self.cmake("vorbis", "libvorbis-1.3.7", ["-DBUILD_TESTING=OFF", "-DCMAKE_PREFIX_PATH=" + self.prefix.as_posix()])
        self.cmake("opus", "opus-1.6", ["-DOPUS_BUILD_TESTING=OFF", "-DOPUS_BUILD_PROGRAMS=OFF", "-DOPUS_INSTALL_PKG_CONFIG_MODULE=ON"])
        source = self.output / "sources/lame-3.100"
        self.run("lame-configure", [str(self.tools / "sh.exe"), "./configure", "--prefix=" + self.prefix.as_posix(),
            "--build=x86_64-w64-mingw32", "--host=x86_64-w64-mingw32", "--disable-shared", "--enable-static",
            "--disable-frontend", "--disable-decoder", "--disable-gtktest",
            "CFLAGS=-O2 -Wno-error=implicit-function-declaration -Wno-error=incompatible-pointer-types"], source)
        self.run("lame-build", [str(self.tools / "make.exe"), "-j" + str(self.jobs)], source)
        self.run("lame-install", [str(self.tools / "make.exe"), "install"], source)

    def ffmpeg(self, *, shared: bool) -> None:
        name = "ffmpeg-shared" if shared else "ffmpeg-static"
        base = self.output / ("sources-shared" if shared else "sources")
        if shared:
            unpack(self.archives["ffmpeg_source"], base)
        source = base / ("ffmpeg-" + self.lock["ffmpeg_version"])
        prefix = self.shared if shared else self.prefix
        flags = ffmpeg_flags(prefix, shared=shared)
        self.run(name + "-configure", [str(self.tools / "sh.exe"), "./configure", *flags], source)
        # Copy build-time DLL aliases on Windows without requiring symlink privileges.
        self.run(name + "-build", [str(self.tools / "make.exe"), "V=1", "LN_S=cp -f", "-j" + str(self.jobs), "ffmpeg.exe", "ffprobe.exe"], source)
        self.run(name + "-install", [str(self.tools / "make.exe"), "V=1", "LN_S=cp -f", "install"], source)
        if shared:
            self.import_libraries()
        metadata = {"configure": flags, "source_sha256": sha256(self.archives["ffmpeg_source"]),
            "config_header_sha256": sha256(source / "config.h"),
            "config_components_sha256": sha256(source / "config_components.h"),
            "objects_retained": True, "source_modified": False}
        (self.output / (name + ".json")).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    def import_libraries(self) -> None:
        tool = msvc_librarian()
        for name in LIBRARIES:
            definitions = list((self.shared / "lib").glob(name + "-*.def"))
            if len(definitions) != 1:
                raise RuntimeError("Missing or ambiguous shared library export definition: " + name)
            self.run("msvc-import-" + name, [str(tool), "/nologo", "/machine:x64",
                "/def:" + str(definitions[0]), "/out:" + str(self.shared / "lib" / (name + ".lib"))], self.output)

    def finish(self) -> dict:
        files = []
        for kind, prefix in (("static", self.prefix), ("shared", self.shared)):
            for suffix in ("*.exe", "*.dll"):
                for path in sorted((prefix / "bin").glob(suffix)):
                    files.append({"kind": kind, "relative_path": path.relative_to(self.output).as_posix(),
                        "sha256": sha256(path), "bytes": path.stat().st_size})
        development_files = []
        for path in sorted((self.shared / "include").rglob("*.h")) + sorted((self.shared / "lib").glob("*.lib")):
            development_files.append({"relative_path": path.relative_to(self.output).as_posix(),
                "sha256": sha256(path), "bytes": path.stat().st_size})
        configurations = {}
        for variant in ("static", "shared"):
            path = self.output / ("ffmpeg-" + variant + ".json")
            if path.exists():
                configurations[variant] = {"sha256": sha256(path), "settings": json.loads(path.read_text())}
        license_paths = ("sources/ffmpeg-" + self.lock["ffmpeg_version"] + "/COPYING.LGPLv2.1",
            "sources/lame-3.100/COPYING", "sources/libogg-1.3.6/COPYING", "sources/libvorbis-1.3.7/COPYING",
            "sources/opus-1.6/COPYING", "toolchain/w64devkit/COPYING.MinGW-w64-runtime.txt")
        licenses = [{"relative_path": relative, "sha256": sha256(self.output / relative),
            "bytes": (self.output / relative).stat().st_size} for relative in license_paths]
        result = {"schema_version": 1, "profile": self.lock["profile"], "lock_sha256": sha256(self.lock_path),
            "recipe_sha256": sha256(Path(__file__)), "ffmpeg_dir": str(self.prefix / "bin"),
            "shared_prefix": str(self.shared), "source_archives_retained": True, "objects_retained": True,
            "byte_reproducibility_verified": False, "files": files,
            "shared_development_files": development_files, "configurations": configurations,
            "source_license_files": licenses,
            "archives": [{"role": item["role"], "filename": item["filename"], "sha256": item["sha256"]} for item in self.lock["archives"]]}
        (self.output / "build-manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/windows-media")
    parser.add_argument("--lock", type=Path, default=ROOT / "packaging/windows-media.lock.json")
    parser.add_argument("--variant", choices=("static", "shared", "both"), default="both")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--fetch-only", action="store_true")
    args = parser.parse_args()
    if os.name != "nt" or sys.version_info < (3, 12) or not 1 <= args.jobs <= 64:
        parser.error("A Windows host, Python 3.12 or newer, and between 1 and 64 build jobs are required")
    output = args.output.resolve()
    if output == ROOT or output in ROOT.parents:
        parser.error("Use a dedicated build output directory")
    lock_path = args.lock.resolve()
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1 or len({item["role"] for item in lock["archives"]}) != len(lock["archives"]):
        parser.error("Invalid media lock")
    output.mkdir(parents=True, exist_ok=True)
    identity = output / "build-lock.sha256"
    digest = sha256(lock_path)
    if identity.exists() and identity.read_text().strip() != digest:
        parser.error("This directory belongs to a different media lock; choose a fresh directory")
    identity.write_text(digest + "\n")
    builder = Builder(output, lock, lock_path, args.jobs)
    builder.prepare(offline=args.offline)
    if args.fetch_only:
        return
    builder.dependencies()
    if args.variant in ("static", "both"):
        builder.ffmpeg(shared=False)
    if args.variant in ("shared", "both"):
        builder.ffmpeg(shared=True)
    print(json.dumps(builder.finish(), indent=2))


if __name__ == "__main__":
    main()
