"""Source-build safeguards without compilers, network requests or model calls."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("windows_media_build", ROOT / "scripts/build_windows_media.py")
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)


def test_source_lock_pins_every_archive_and_retains_toolchain_sources():
    lock = json.loads((ROOT / "packaging/windows-media.lock.json").read_text())
    roles = {item["role"] for item in lock["archives"]}
    assert {"toolchain_source", "toolchain_binary", "toolchain_recipe", "ffmpeg_source",
            "lame_source", "ogg_source", "vorbis_source", "opus_source",
            "assembler_source", "assembler_binary"} <= roles
    assert len(roles) == len(lock["archives"])
    for item in lock["archives"]:
        assert media.checked_archive_name(item["filename"]) == item["filename"]
        assert len(item["sha256"]) == 64 and int(item["sha256"], 16) >= 0
        assert item["url"].startswith("https://") and item["bytes"] > 0


def test_profile_preserves_audio_export_encoders_without_optional_gpl_dependencies(tmp_path):
    for shared in (False, True):
        flags = media.ffmpeg_flags(tmp_path, shared=shared)
        for required in ("--disable-autodetect", "--disable-gpl", "--disable-nonfree", "--disable-network",
                         "--enable-libmp3lame", "--enable-libvorbis", "--enable-libopus", "--enable-ffprobe"):
            assert required in flags
        assert ("--enable-shared" in flags) is shared
        assert ("--enable-static" in flags) is not shared
        assert not any(flag.startswith(("--enable-libx264", "--enable-libx265", "--enable-nonfree")) for flag in flags)


def test_offline_fetch_requires_exact_existing_bytes(tmp_path):
    path = tmp_path / "source.tar"
    path.write_bytes(b"original")
    item = {"filename": path.name, "sha256": hashlib.sha256(b"original").hexdigest(),
            "url": "https://example.invalid/source.tar", "bytes": 8}
    assert media.fetch(item, tmp_path, offline=True) == path
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum differs"):
        media.fetch(item, tmp_path, offline=True)
    with pytest.raises(ValueError, match="plain filenames"):
        media.fetch(dict(item, filename="../escape.tar"), tmp_path, offline=True)


def test_zip_cannot_escape_source_directory(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("../escaped.txt", "bad")
    with pytest.raises(ValueError, match="escapes"):
        media.unpack(archive, tmp_path / "sources")
    assert not (tmp_path / "escaped.txt").exists()


@pytest.mark.skipif(not hasattr(tarfile, "data_filter"), reason="Safe tar extraction requires a current Python security release")
def test_tar_cannot_escape_source_directory(tmp_path):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as source:
        entry = tarfile.TarInfo("../escaped.txt")
        entry.size = 3
        source.addfile(entry, io.BytesIO(b"bad"))
    with pytest.raises(tarfile.FilterError):
        media.unpack(archive, tmp_path / "sources")
    assert not (tmp_path / "escaped.txt").exists()


@pytest.mark.parametrize('edition', ['BuildTools', 'Enterprise'])
def test_msvc_discovery_supports_local_and_hosted_runner_editions(tmp_path, edition):
    tool = tmp_path / f'programs/Microsoft Visual Studio/2022/{edition}/VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64/lib.exe'
    tool.parent.mkdir(parents=True)
    tool.write_bytes(b'compiler')
    env = {'ProgramFiles': str(tmp_path / 'programs'), 'ProgramFiles(x86)': str(tmp_path / 'other')}
    assert media.msvc_librarian(env) == tool
    explicit = {'VSINSTALLDIR': str(tool.parents[7]), 'ProgramFiles': str(tmp_path / 'missing'),
                'ProgramFiles(x86)': str(tmp_path / 'missing')}
    assert media.msvc_librarian(explicit) == tool
