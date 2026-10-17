#!/usr/bin/env python3
"""Measure one uncached CUDA pipeline against consumed reference labels.

An explicitly supplied adapter owns the experimental model lifecycle. See
castwell.speed_benchmark.run_benchmark for its adapter interface.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from castwell.speed_benchmark import run_benchmark


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True, help="Consumed word-reference policy overlay")
    parser.add_argument("--configuration", type=Path, required=True, help="Frozen configuration without credentials")
    parser.add_argument("--adapter-file", type=Path, required=True, help="Trusted local Python model-lifecycle adapter")
    parser.add_argument("--adapter-factory", default="build_adapter")
    parser.add_argument("--model-file", type=Path, action="append", default=[])
    parser.add_argument("--source-file", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True, help="Must not exist")
    parser.add_argument("--transcript", type=Path, help="Explicit cached-ASR diagnostic; cannot pass the cold timing goal")
    parser.add_argument("--candidate-preview", action="store_true", help="Render proposed cuts in a separate preview, without application approval")
    parser.add_argument("--target-seconds-per-hour", type=float, default=300.)
    args = parser.parse_args(argv)
    try:
        spec = importlib.util.spec_from_file_location("castwell_benchmark_candidate", args.adapter_file)
        if spec is None or spec.loader is None:
            raise ValueError("Cannot load adapter file")
        # The adapter module must define its factory without starting models at
        # import time. Model setup belongs inside the factory/prepare stopwatch.
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        adapter_factory = getattr(module, args.adapter_factory)
        result = run_benchmark(audio=args.audio, reference=args.reference,
            configuration=json.loads(args.configuration.read_text(encoding="utf-8")), output_dir=args.output_dir,
            adapter_factory=adapter_factory, model_files=args.model_file,
            source_files=[Path(__file__), ROOT / "castwell/speed_benchmark.py", args.adapter_file, *args.source_file],
            transcript_path=args.transcript, candidate_preview=args.candidate_preview,
            target_seconds_per_hour=args.target_seconds_per_hour)
    except (ValueError, OSError, KeyError, AttributeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": result["status"], "elapsed_seconds": result["elapsed_seconds"],
        "seconds_per_audio_hour": result["timing"]["observed_seconds_per_audio_hour"],
        "cold_cuda_timing_target_met": result["cold_cuda_timing_target_met"],
        "production_accuracy_passed": False}))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
