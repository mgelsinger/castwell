"""One-recording timing and consumed-reference scoring for CUDA experiments.

The adapter owns model lifecycle and must provide runtime evidence. This runner
does not infer CUDA placement, a cold model start, or acoustic truth from a name.
No library state is changed, and every output directory must be new.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time

from . import processing
from .ad_scope import REMOVAL_SCOPE
from .evaluation import union_intervals
from .recording_evaluation import sha256

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ("commercial", "protected", "ambiguous")


def _save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def budget_result(duration, elapsed, *, complete, uncached, target=300.0):
    """Normalize observed wall time, never a sum of independently timed phases."""
    duration, elapsed, target = (_number(value, name) for value, name in
        ((duration, "duration"), (elapsed, "elapsed"), (target, "target")))
    if duration <= 0 or elapsed < 0 or target <= 0:
        raise ValueError("Duration and target must be positive; elapsed cannot be negative")
    normalized = elapsed * 3600 / duration
    return {"target_seconds_per_audio_hour": target,
            "observed_seconds_per_audio_hour": normalized,
            "episode_budget_seconds": duration * target / 3600,
            "complete_pipeline": bool(complete), "no_transcript_or_prediction_reuse": bool(uncached),
            "timing_target_met": bool(complete and uncached and normalized <= target)}


def load_consumed_reference(path, audio, *, root=ROOT):
    """Explicitly adapt the two historical word-reference formats, without edits.

    New ASR can differ from reference ASR. Scores compare original audio times,
    not new word IDs, and remain provisional consumed development evidence.
    """
    path, audio, root = Path(path).resolve(), Path(audio).resolve(), Path(root)
    reference = json.loads(path.read_text(encoding="utf-8"))
    if (reference.get("detector_predictions_seen") is not True
            or reference.get("acceptance_eligible") is not False):
        raise ValueError("This adapter accepts explicitly consumed, acceptance-ineligible references only")
    if reference.get("removal_scope") != REMOVAL_SCOPE:
        raise ValueError("Reference must declare the current removal scope")
    if sha256(audio) != reference["source_sha256"]:
        raise ValueError("Reference source audio hash differs")
    transcript_path = Path(reference.get("transcript_path", reference.get("transcript", "")))
    if not transcript_path.is_absolute():
        transcript_path = root / transcript_path
    if sha256(transcript_path) != reference["transcript_sha256"]:
        raise ValueError("Frozen reference transcript changed")
    transcript = processing.validate_transcript(json.loads(transcript_path.read_text(encoding="utf-8")))
    flat = [word for segment in transcript["segments"] for word in segment.get("words", [])]
    if not flat:
        raise ValueError("Reference requires word alignment")
    if "words" in reference:
        words = deepcopy(reference["words"])
        if len(words) != len(flat) or any(
                row.get("global_word_index") != index
                or any(row.get(key) != word.get(key) for key in ("word", "start", "end"))
                for index, (row, word) in enumerate(zip(words, flat))):
            raise ValueError("Reference word table differs from its frozen transcript")
        reference_format = "word-table"
    else:
        words = [dict(word, global_word_index=index, scored=True, category=None)
                 for index, word in enumerate(flat)]
        for category, name in (("commercial", "commercial_units"), ("protected", "protected"),
                               ("ambiguous", "ambiguous_promotional_units")):
            for unit in reference[name]:
                first, last = unit["global_word_index_range_inclusive"]
                if (type(first) is not int or type(last) is not int
                        or not 0 <= first <= last < len(words)):
                    raise ValueError("Invalid reference word locator")
                for word in words[first:last + 1]:
                    if word["category"] is not None:
                        raise ValueError("Reference word categories overlap")
                    word.update(category=category, unit_id=unit["id"])
        reference_format = "full-episode-word-ranges"
    for word in words:
        if word.get("category") not in CATEGORIES or type(word.get("scored", True)) is not bool:
            raise ValueError("Every reference word requires a known category and boolean scoring flag")
    source = reference.get("source")
    source_duration = source.get("source_duration_seconds") if isinstance(source, dict) else reference.get("audio_seconds")
    if source_duration is None:
        raise ValueError("Reference needs the full source audio duration")
    duration = _number(source_duration, "reference source duration")
    if duration <= 0 or any(word["start"] < 0 or word["end"] > duration + .01 for word in words):
        raise ValueError("Reference word times are outside source audio")
    return {"words": words, "duration": duration, "unknown_gaps": reference.get("unknown_gaps", []),
            "provenance": {"reference_sha256": sha256(path), "reference_transcript_sha256": sha256(transcript_path),
                "source_audio_sha256": reference["source_sha256"], "reference_format": reference_format,
                "removal_scope": REMOVAL_SCOPE, "consumed_development": True, "acceptance_eligible": False,
                "human_listening_verified_declaration": reference.get("human_listening_verified") is True,
                "payment_verified_declaration": reference.get("payment_verified") is True,
                "new_asr_word_identity_match_required": False}}


def score_words(cuts, reference):
    """Count partial-word damage and missed speech, including zero-time entries."""
    spans = union_intervals(cuts, reference["duration"])
    scores, errors = {}, {"commercial_missed": [], "protected_selected": []}
    for category in CATEGORIES:
        rows = [row for row in reference["words"] if row["category"] == category and row.get("scored", True)]
        values = {"word_entries": len(rows), "word_seconds": 0.0, "word_seconds_selected": 0.0,
                  "word_entries_any_selected": 0, "word_entries_fully_selected": 0,
                  "zero_duration_word_entries": 0, "zero_duration_points_selected": 0}
        for word in rows:
            start, end = word["start"], word["end"]
            seconds = end - start
            selected = sum(max(0., min(end, span["end"]) - max(start, span["start"])) for span in spans)
            zero_selected = seconds == 0 and any(span["start"] <= start < span["end"] for span in spans)
            touched = selected > 1e-7 or zero_selected
            fully = math.isclose(selected, seconds, abs_tol=1e-7, rel_tol=0) if seconds > 0 else zero_selected
            values["word_seconds"] += seconds
            values["word_seconds_selected"] += selected
            values["word_entries_any_selected"] += touched
            values["word_entries_fully_selected"] += fully
            values["zero_duration_word_entries"] += seconds == 0
            values["zero_duration_points_selected"] += zero_selected
            error = "commercial_missed" if category == "commercial" and not fully else (
                "protected_selected" if category == "protected" and touched else None)
            if error:
                errors[error].append({"global_word_index": word["global_word_index"], "start": start, "end": end,
                                      "selected_seconds": selected, "word": word["word"], "unit_id": word.get("unit_id")})
        values["word_seconds_unselected"] = max(0., values["word_seconds"] - values["word_seconds_selected"])
        values["word_entries_not_fully_selected"] = len(rows) - values["word_entries_fully_selected"]
        scores[category] = values
    return {"categories": scores, "errors": errors,
            "zero_reference_word_errors": not any(errors.values()),
            "selected_audio_seconds": sum(span["end"] - span["start"] for span in spans),
            "acoustic_accuracy_verified": False, "acceptance_eligible": False}


class RequestTelemetry:
    """Adapter-supplied request observations; missing token usage stays unknown."""
    def __init__(self):
        self.rows = []

    def record_request(self, *, elapsed_seconds, succeeded, input_tokens=None, output_tokens=None,
                       cached=False, paid=False, stage="classifier"):
        elapsed = _number(elapsed_seconds, "request elapsed")
        if elapsed < 0 or any(type(value) is not bool for value in (succeeded, cached, paid)):
            raise ValueError("Request timing and status flags are invalid")
        for value in (input_tokens, output_tokens):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Token counts must be nonnegative integers or unknown")
        self.rows.append({"elapsed_seconds": elapsed, "succeeded": succeeded,
                          "input_tokens": input_tokens, "output_tokens": output_tokens,
                          "cached": cached, "paid": paid, "stage": str(stage)})

    def summary(self):
        return {"requests": len(self.rows), "failures": sum(not row["succeeded"] for row in self.rows),
                "cache_hits": sum(row["cached"] for row in self.rows), "paid_requests": sum(row["paid"] for row in self.rows),
                "request_wall_seconds_sum": sum(row["elapsed_seconds"] for row in self.rows),
                "token_usage_complete": all(row["input_tokens"] is not None and row["output_tokens"] is not None for row in self.rows),
                "known_input_tokens": sum(row["input_tokens"] or 0 for row in self.rows),
                "known_output_tokens": sum(row["output_tokens"] or 0 for row in self.rows)}


def _reject_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in {"key", "api_key", "ai_key", "jev_api_key", "authorization", "access_token"} and child:
                raise ValueError("Benchmark configuration must not contain credentials")
            _reject_secrets(child)
    elif isinstance(value, list):
        for child in value:
            _reject_secrets(child)


def run_benchmark(*, audio, reference, configuration, output_dir, adapter_factory,
                  model_files=(), source_files=(), transcript_path=None, candidate_preview=False,
                  target_seconds_per_hour=300.0, clock=time.perf_counter):
    """Time a new output, including adapter/model setup and all processing phases.

    factory(context) returns an object implementing prepare(), transcribe(audio),
    detect(transcript), render(audio, output, cuts), evidence(), close(). An
    optional prepare_episode(audio) hook measures waveform creation and local
    episode/database setup inside the same stopwatch. Without it the report is
    explicitly a processing-pipeline measurement, not complete Jobs overhead.
    The context contains configuration, output_dir and telemetry. No adapter may see
    labels through this interface. Adapter code is trusted, not sandboxed.
    """
    audio, reference, output_dir = Path(audio).resolve(), Path(reference).resolve(), Path(output_dir).resolve()
    _reject_secrets(configuration)
    budget_result(1, 0, complete=False, uncached=False, target=target_seconds_per_hour)
    if output_dir.exists():
        raise ValueError("Preserve previous evidence: output directory must be new")
    if output_dir == audio.parent or audio.is_relative_to(output_dir) or reference.is_relative_to(output_dir):
        raise ValueError("Benchmark outputs must be separate from inputs")
    preflight_started = clock()
    frozen = load_consumed_reference(reference, audio)
    identities = {"models": {str(Path(path).resolve()): sha256(path) for path in model_files},
                  "sources": {str(Path(path).resolve()): sha256(path) for path in source_files}}
    actual_duration = processing.probe_duration(audio)
    if not math.isclose(actual_duration, frozen["duration"], abs_tol=.05, rel_tol=0):
        raise ValueError("Reference duration differs from actual source audio")
    transcript_identity = None
    if transcript_path is not None:
        transcript_path = Path(transcript_path).resolve()
        transcript_identity = sha256(transcript_path)
    config_copy = deepcopy(configuration)
    config_hash = hashlib.sha256(json.dumps(config_copy, sort_keys=True, allow_nan=False).encode()).hexdigest()
    output_dir.mkdir(parents=True)
    report = {"schema_version": 1, "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running", "configuration": config_copy, "configuration_sha256": config_hash,
        "identities": identities, "reference": frozen["provenance"], "audio_seconds": actual_duration,
        "transcript_reused": transcript_path is not None, "input_transcript_sha256": transcript_identity,
        "render_mode": "candidate_preview" if candidate_preview else "approved_cuts",
        "application_library_modified": False,
        "episode_preparation_included": False,
        "timing_scope": "processing_pipeline_without_application_queue_and_database_overhead",
        "preflight_hash_and_probe_seconds_excluded": clock() - preflight_started,
        "asset_downloads_in_timed_run_allowed": False, "phases": {},
        "limitations": ["Consumed provisional ASR references do not establish listening-verified acoustic boundaries or production accuracy.",
            "Input audio and model assets must already exist; downloads and preflight hashing are excluded and reported separately.",
            "Cold processing includes model setup, fresh speech recognition, detection, and rendering. OS disk caching is not flushed.",
            "Runtime and request evidence is supplied by the trusted adapter; hashing a model file does not attest which weights a remote server loaded.",
            "Candidate preview renders proposals for measurement and listening only; it does not approve cuts in the application."]}
    _save(output_dir / "report.private.json", report)
    telemetry, adapter, complete = RequestTelemetry(), None, False
    started = clock()

    def phase(name, call):
        begin = clock()
        try:
            result = call()
        except Exception:
            report["phases"][name] = {"seconds": clock() - begin, "complete": False}
            raise
        report["phases"][name] = {"seconds": clock() - begin, "complete": True}
        return result

    try:
        adapter = phase("adapter_setup", lambda: adapter_factory({"configuration": deepcopy(config_copy),
            "output_dir": output_dir, "telemetry": telemetry}))
        episode_preparation = getattr(adapter, "prepare_episode", None)
        if episode_preparation is not None:
            if not callable(episode_preparation):
                raise ValueError('Adapter episode preparation must be callable')
            report['episode_preparation'] = phase('episode_preparation', lambda: episode_preparation(audio))
            report['episode_preparation_included'] = True
            report['timing_scope'] = 'processing_pipeline_with_adapter_episode_preparation'
        phase("model_prepare", adapter.prepare)
        if transcript_path is None:
            transcript = phase("transcription", lambda: adapter.transcribe(audio))
        else:
            transcript = phase("cached_transcript_load", lambda: json.loads(transcript_path.read_text(encoding="utf-8")))
        transcript = processing.validate_transcript(transcript, actual_duration)
        _save(output_dir / "transcript.private.json", transcript)
        cuts = processing.validate_cuts(phase("detection", lambda: adapter.detect(transcript)), actual_duration)
        _save(output_dir / "cuts.private.json", cuts)
        rendered_cuts = [dict(cut, approved=True) for cut in cuts] if candidate_preview else deepcopy(cuts)
        output = output_dir / "preview.mp3"
        report["render"] = phase("render", lambda: adapter.render(audio, output, rendered_cuts))
        if not output.is_file() or output.stat().st_size == 0:
            raise ValueError("Renderer did not create a nonempty output file")
        complete = True
        report["status"] = "complete"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        elapsed = clock() - started
        try:
            report["runtime_evidence"] = adapter.evidence() if adapter is not None else {}
        except Exception as exc:
            report["runtime_evidence"] = {}
            report["evidence_error"] = type(exc).__name__
        if adapter is not None:
            try:
                phase("cleanup_excluded", adapter.close)
            except Exception as exc:
                report["cleanup_error"] = {"type": type(exc).__name__, "message": str(exc)}
                report["status"] = "failed"
                complete = False
        runtime = report["runtime_evidence"]
        observed = telemetry.summary()
        uncached = (transcript_path is None and observed["cache_hits"] == 0
                    and runtime.get("transcript_cache_hits") == 0 and runtime.get("prediction_cache_hits") == 0)
        report["elapsed_seconds"] = elapsed
        report["requests"] = {"summary": observed, "observations": telemetry.rows}
        report["timing"] = budget_result(actual_duration, elapsed, complete=complete, uncached=uncached,
                                         target=target_seconds_per_hour)
        report["cuda_only_verified_by_adapter"] = runtime.get("cuda_only_verified") is True
        report["models_cold_at_start_verified_by_adapter"] = runtime.get("models_cold_at_start") is True
        report["cold_cuda_timing_target_met"] = bool(report["timing"]["timing_target_met"]
            and report["cuda_only_verified_by_adapter"] and report["models_cold_at_start_verified_by_adapter"]
            and runtime.get("asset_downloads") == 0 and runtime.get("request_telemetry_complete") is True)
        report["original_preserved"] = sha256(audio) == frozen["provenance"]["source_audio_sha256"]
        if not report["original_preserved"]:
            report["status"] = "failed"
            report["cold_cuda_timing_target_met"] = False
        if complete:
            report["scores"] = {"candidates": score_words(cuts, frozen),
                "rendered": score_words([cut for cut in rendered_cuts if cut["approved"]], frozen)}
            report["output_sha256"] = sha256(output)
        report["production_accuracy_passed"] = False
        _save(output_dir / "report.private.json", report)
    return report
