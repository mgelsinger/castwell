"""Download and verify one pinned public Qwen3.8-27B Q4_K_M artifact.

No account, API key, model inference, or local requantization is used. Existing
files and interrupted downloads are preserved rather than overwritten.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "unsloth/Qwen3.8-27B-GGUF"
REVISION = "2c80088ea5e6033bed6a180e28a6573d98e8c0cf"
MODEL_NAME = "Qwen3.8-27B-Q4_K_M.gguf"
MODEL_SIZE = 17_106_775_008
MODEL_SHA256 = "7e78da5d7e3ae28d178121f58646953305f3e5bd3cb46f4a75584e8b6c6fe169"
LICENSE = "apache-2.0"
PROVENANCE_NAME = "download-provenance.json"


def model_url() -> str:
    return f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{MODEL_NAME}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model(path: Path) -> None:
    if not path.is_file() or path.stat().st_size != MODEL_SIZE:
        raise ValueError(f"Model missing or wrong size; existing files are unchanged: {path}")
    if sha256(path) != MODEL_SHA256:
        raise ValueError(f"Model SHA256 mismatch; existing file is unchanged: {path}")


def identity(path: Path) -> dict:
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "revision": REVISION,
        "filename": MODEL_NAME,
        "url": model_url(),
        "model_path": str(path),
        "size_bytes": MODEL_SIZE,
        "sha256": MODEL_SHA256,
        "license": LICENSE,
        "base_model": "Qwen/Qwen3.8-27B",
        "origin": "Published Unsloth Q4_K_M; no local requantization",
        "origin_limit": "The publisher declares the original Qwen base and supplies BF16 exports. Its exact conversion chain is not independently attested.",
    }


def verify_provenance(path: Path, model: Path) -> None:
    saved = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(saved, dict) or any(saved.get(key) != value for key, value in identity(model).items()):
        raise ValueError("Existing download provenance does not match the pinned artifact; it is unchanged")


def promote(partial: Path, target: Path) -> None:
    # A same-directory hard link publishes complete bytes without replacing a
    # file that appeared while the download was running, on Windows or POSIX.
    try:
        os.link(partial, target)
    except FileExistsError:
        raise ValueError("The destination appeared during setup; both files are preserved") from None
    except OSError:
        raise ValueError("Could not publish the verified file without replacement; partial is preserved") from None
    partial.unlink()


def download(partial: Path) -> None:
    import requests

    if partial.exists():
        raise ValueError(f"Interrupted download exists; inspect it or choose another directory: {partial}")
    # A new session with trust_env=False excludes tokens, netrc and proxies.
    with requests.Session() as session:
        session.trust_env = False
        session.headers["User-Agent"] = "Castwell-public-model-setup/1"
        try:
            with session.get(model_url(), stream=True, timeout=(30, 120)) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("Unexpected download response; no existing files were replaced")
                length = response.headers.get("Content-Length")
                if length is not None and int(length) != MODEL_SIZE:
                    raise ValueError("Publisher response has an unexpected size")
                received, last_report = 0, time.monotonic()
                with partial.open("xb") as stream:
                    for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                        if not chunk:
                            continue
                        if received + len(chunk) > MODEL_SIZE:
                            raise ValueError("Download exceeds the pinned size; partial is preserved")
                        stream.write(chunk)
                        received += len(chunk)
                        now = time.monotonic()
                        if now - last_report >= 30:
                            print(f"Downloaded {received:,} / {MODEL_SIZE:,} bytes", flush=True)
                            last_report = now
                    stream.flush()
                    os.fsync(stream.fileno())
        except requests.RequestException:
            raise ValueError("Public model download failed; any partial file is preserved for inspection") from None


def prepare(directory: Path, *, verify_only: bool = False) -> dict:
    directory = directory.expanduser().resolve()
    model = directory / MODEL_NAME
    partial = directory / (MODEL_NAME + ".partial")
    provenance = directory / PROVENANCE_NAME
    if provenance.exists():
        verify_provenance(provenance, model)
    if model.exists():
        verify_model(model)
    elif verify_only:
        raise ValueError(f"Model is missing; verify-only never downloads: {model}")
    else:
        if partial.exists():
            raise ValueError(f"Interrupted download exists; inspect it or choose another directory: {partial}")
        directory.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {MODEL_SIZE:,} public bytes from pinned {REPOSITORY}", flush=True)
        download(partial)
        print("Verifying the complete saved file SHA256", flush=True)
        verify_model(partial)
        promote(partial, model)
    result = dict(identity(model), verified_at_utc=datetime.now(timezone.utc).isoformat())
    if not verify_only and not provenance.exists():
        temporary = directory / (PROVENANCE_NAME + ".partial")
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        promote(temporary, provenance)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / ".local/models/qwen3.8-27b")
    parser.add_argument("--verify-only", action="store_true", help="Hash the existing pinned model without downloads or file changes")
    args = parser.parse_args(argv)
    try:
        result = prepare(args.directory, verify_only=args.verify_only)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
