"""Fetch hash-pinned public build dependencies, without installing anything."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def download(url: str, target: Path, expected: str | None = None) -> str:
    if target.exists() and expected:
        with target.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() == expected:
                return expected
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    request = urllib.request.Request(url, headers={"User-Agent": "Castwell-Windows-build/0.5.1"})
    with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as output:
        shutil.copyfileobj(response, output, 1024 * 1024)
    with partial.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if expected and digest != expected:
        partial.unlink()
        raise RuntimeError(f"Checksum mismatch for {target.name}")
    partial.replace(target)
    return digest


def extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            path = (destination / member.filename).resolve()
            if not path.is_relative_to(destination.resolve()):
                raise RuntimeError("Archive contains a path outside its destination")
        source.extractall(destination)


def write_native_notices(notices: Path, metadata: dict) -> None:
    (notices / "THIRD-PARTY-NOTICES.txt").write_text(
        "Castwell Windows distributions\n\n"
        "Python and Python dependencies retain their bundled licenses.\n"
        "FFmpeg and llama.cpp are separate, unmodified third-party executables.\n"
        "Speech and classifier weights are not included and retain their publishers' terms.\n"
        "The built-in Silero VAD model is included under the Silero Team MIT license.\n\n"
        + "\n\n".join(f"{name}\nLicense: {item['license']}\nArtifact: {item['url']}\n"
                        f"SHA256: {item['sha256']}\nSource: {item['source']}" for name, item in metadata.items())
        + "\n\nFFmpeg Windows build documentation and dependency sources:\n"
          "https://www.gyan.dev/ffmpeg/builds/\n"
          "https://github.com/GyanD/codexffmpeg\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local" / "windows-vendor")
    args = parser.parse_args()
    output = args.output.resolve()
    metadata = json.loads((ROOT / "packaging" / "windows-runtime.json").read_text())
    notices = output / "notices"
    notices.mkdir(parents=True, exist_ok=True)
    for name, item in metadata.items():
        if item.get("kind") == "notice":
            target = notices / item["filename"]
            download(item["url"], target, item["sha256"])
            item["download_path"] = str(target)
            continue
        archive = output / "downloads" / item["url"].rsplit("/", 1)[1]
        download(item["url"], archive, item["sha256"])
        if archive.suffix == ".zip":
            extract(archive, output / name)
        item["download_path"] = str(archive)
    licenses = {
        "FFmpeg-GPL-3.0.txt": "https://raw.githubusercontent.com/FFmpeg/FFmpeg/n8.1.2/COPYING.GPLv3",
        "llama.cpp-MIT.txt": "https://raw.githubusercontent.com/ggml-org/llama.cpp/b11146/LICENSE",
        "Inno-Setup.txt": "https://raw.githubusercontent.com/jrsoftware/issrc/is-6_7_3/license.txt",
        "CTranslate2-MIT.txt": "https://raw.githubusercontent.com/OpenNMT/CTranslate2/v4.8.2/LICENSE",
        "FlatBuffers-Apache-2.0.txt": "https://raw.githubusercontent.com/google/flatbuffers/v25.12.19/LICENSE",
        "Tokenizers-Apache-2.0.txt": "https://raw.githubusercontent.com/huggingface/tokenizers/v0.23.2/LICENSE",
    }
    for name, url in licenses.items():
        download(url, notices / name)
    for name in metadata:
        if name == "inno" or metadata[name].get("kind") == "notice":
            continue
        for source in (output / name).rglob("*"):
            if source.is_file() and ("license" in source.name.lower() or source.name.lower().startswith("readme")):
                shutil.copy2(source, notices / f"{name}-{source.name}")
    write_native_notices(notices, metadata)
    ffmpeg_directory = next((output / "ffmpeg").rglob("ffmpeg.exe")).parent
    llama_directory = next((output / "llama").rglob("llama-server.exe")).parent
    cuda_directory = output / "cuda-runtime"
    cuda_directory.mkdir(parents=True, exist_ok=True)
    for name in ("cuda", "cudnn", "cufft", "curand", "nvrtc"):
        for binary in (output / name).rglob("*.dll"):
            shutil.copy2(binary, cuda_directory / binary.name)
    download("https://developer.download.nvidia.com/compute/cuda/redist/libcublas/LICENSE.txt", notices / "NVIDIA-CUDA.txt")
    result = {"ffmpeg_dir": str(ffmpeg_directory), "llama_dir": str(llama_directory), "cuda_dir": str(cuda_directory),
              "notices_dir": str(notices), "inno_installer": metadata["inno"]["download_path"],
              "dependencies": metadata}
    (output / "vendor-manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
