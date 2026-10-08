"""Build a pinned Qwen3.5 candidate using official ggml-org source weights.

This downloads free public weights and runs CPU quantization. It does not start
an inference server. Q8-to-Q4 requantization is a quality tradeoff; the output is
a local conversion, not a Qwen-published Q4 release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "ggml-org/Qwen3.5-35B-A3B-GGUF"
REVISION = "3127ef0b7fd4f12626506419ceefde54fa8db53c"
SOURCE_NAME = "Qwen3.5-35B-A3B-Q8_0.gguf"
SOURCE_SIZE = 36_903_139_584
SOURCE_SHA256 = "8a83fbfde74b0366feb76b075c10df46bbece6aa21ed14a6f291a7904f2b7d67"
OUTPUT_NAME = "Qwen3.5-35B-A3B-Q4_K_M-local.gguf"
QUANTIZER_SHA256 = "dee025c8bd2b3e4679b766aa8fdac9fa393edd8259840f58af8832bf20708cf7"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_source(path: Path) -> None:
    if not path.is_file() or path.stat().st_size != SOURCE_SIZE:
        raise ValueError(f"Source missing or wrong size: {path}")
    if sha256(path) != SOURCE_SHA256:
        raise ValueError(f"Source SHA256 mismatch: {path}")


def verify_provenance(provenance: dict, source: Path, output: Path, quantizer: Path) -> None:
    expected = {
        "repository": REPOSITORY, "revision": REVISION,
        "source_url": f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{SOURCE_NAME}",
        "source_path": str(source), "source_size_bytes": SOURCE_SIZE, "source_sha256": SOURCE_SHA256,
        "conversion": "Q8_0 to Q4_K_M requantization; not original BF16 quantization",
        "quantizer_version": "llama.cpp b11146 (7fe450e19)",
        "quantizer_path": str(quantizer), "quantizer_sha256": QUANTIZER_SHA256,
        "output_path": str(output), "output_size_bytes": output.stat().st_size,
    }
    if any(provenance.get(key) != value for key, value in expected.items()):
        raise ValueError("Saved conversion identity does not match the pinned recipe")
    command = provenance.get("conversion_command")
    prefix = [str(quantizer), "--allow-requantize", str(source), str(output) + ".partial", "Q4_K_M"]
    if (not isinstance(command, list) or len(command) != 6 or command[:5] != prefix
            or not isinstance(command[5], str) or not command[5].isdigit()
            or not 1 <= int(command[5]) <= 128):
        raise ValueError("Saved conversion command does not match the pinned recipe")
    if sha256(output) != provenance.get("output_sha256"):
        raise ValueError("Saved conversion output SHA256 mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / ".local/models/qwen3.5-35b-a3b")
    parser.add_argument("--quantizer", type=Path, default=ROOT / ".local/llama.cpp/llama-quantize.exe")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--verify-only", action="store_true", help="Verify saved source and conversion without downloads")
    args = parser.parse_args()
    if not 1 <= args.threads <= 128:
        parser.error("--threads must be between 1 and 128")
    directory = args.directory.resolve()
    quantizer = args.quantizer.resolve()
    source = directory / SOURCE_NAME
    output = directory / OUTPUT_NAME
    provenance_path = directory / "conversion-provenance.json"
    if not quantizer.is_file() or sha256(quantizer) != QUANTIZER_SHA256:
        raise ValueError("Expected the verified Windows x64 llama.cpp b11146 quantizer")

    directory.mkdir(parents=True, exist_ok=True)
    if not source.exists() and not args.verify_only:
        os.environ.setdefault("HF_HOME", str(ROOT / ".local/models/huggingface"))
        os.environ.setdefault("HF_XET_CACHE", str(ROOT / ".local/models/xet"))
        os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
        from huggingface_hub import hf_hub_download

        print(f"Downloading {SOURCE_SIZE:,} bytes from pinned {REPOSITORY}", flush=True)
        hf_hub_download(REPOSITORY, SOURCE_NAME, revision=REVISION, local_dir=directory, token=False)
    print("Verifying source SHA256", flush=True)
    verify_source(source)
    if output.exists():
        if not provenance_path.is_file():
            raise ValueError("Output exists without conversion provenance; choose a new directory")
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        verify_provenance(provenance, source, output, quantizer)
        print(json.dumps(provenance, indent=2), flush=True)
        return 0
    if args.verify_only:
        raise ValueError("Converted model is missing")

    # No GPU model is loaded. Preserve an interrupted conversion for inspection.
    partial = directory / (OUTPUT_NAME + ".partial")
    if partial.exists():
        raise ValueError(f"Interrupted conversion already exists: {partial}")
    command = [str(quantizer), "--allow-requantize", str(source), str(partial), "Q4_K_M", str(args.threads)]
    print("CPU conversion: " + subprocess.list2cmdline(command), flush=True)
    started = time.monotonic()
    subprocess.run(command, cwd=quantizer.parent, check=True)
    output_sha = sha256(partial)
    provenance = {
        "repository": REPOSITORY, "revision": REVISION,
        "source_url": f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{SOURCE_NAME}",
        "source_path": str(source), "source_size_bytes": SOURCE_SIZE, "source_sha256": SOURCE_SHA256,
        "conversion": "Q8_0 to Q4_K_M requantization; not original BF16 quantization",
        "quantizer_version": "llama.cpp b11146 (7fe450e19)",
        "quantizer_path": str(quantizer), "quantizer_sha256": QUANTIZER_SHA256,
        "conversion_command": command, "conversion_seconds": round(time.monotonic() - started, 3),
        "output_path": str(output), "output_size_bytes": partial.stat().st_size, "output_sha256": output_sha,
    }
    partial.rename(output)
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(provenance, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
