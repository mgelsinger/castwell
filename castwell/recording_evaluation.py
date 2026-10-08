"""Private, checkpointed evaluation against partial real-recording annotations.

Only annotated commercial/protected intervals are scored. Unannotated audio is
not presumed editorial, and provisional transcript anchors are not acoustic gold.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlsplit, urlunsplit

from . import processing
from .evaluation import union_intervals, validate_endpoint

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ("candidates", "verified", "approved")
LIMITATIONS = [
    "References are independently annotated from publisher context and ASR, without human listening verification.",
    "Only labeled intervals are scored. Unverified intervals are excluded; unlabeled audio is not assumed editorial.",
    "Unit coverage concerns provisional transcript spans, not exact acoustic cut boundaries or real-world accuracy.",
    "Candidates are each backend's proposed removal intervals. Verified AI can propose uncertain spans; Kev proposes units whose selected class is commercial and keeps other typed decisions in the private report.",
    "Review is enabled: actual approved cuts should be empty. Zero approved deletion does not establish detection quality.",
    "Model artifact hashes identify supplied local files; this evaluator cannot attest which bytes a separate server loaded.",
    "Four selected shows and overlapping episode context are a small convenience sample, not host-held-out population evidence.",
]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _seconds(spans):
    return sum(span["end"] - span["start"] for span in spans)


def _intersection(left, right):
    return [{"start": max(a["start"], b["start"]), "end": min(a["end"], b["end"])}
            for a in left for b in right if max(a["start"], b["start"]) < min(a["end"], b["end"])]


def _subtract(spans, excluded):
    result = []
    for span in spans:
        parts = [span]
        for mask in excluded:
            next_parts = []
            for part in parts:
                if mask["end"] <= part["start"] or mask["start"] >= part["end"]:
                    next_parts.append(part)
                else:
                    if part["start"] < mask["start"]:
                        next_parts.append({"start": part["start"], "end": mask["start"]})
                    if mask["end"] < part["end"]:
                        next_parts.append({"start": mask["end"], "end": part["end"]})
            parts = next_parts
        result.extend(parts)
    return result


def annotation_intervals(reference, duration):
    excluded = union_intervals(reference.get("unverified_intervals", []), duration)
    commercial = _subtract(union_intervals(reference["commercial_units"], duration), excluded)
    protected = _subtract(union_intervals(reference["protected_units"], duration), excluded)
    if _intersection(commercial, protected):
        raise ValueError("Commercial and protected annotations overlap outside exclusions")
    return commercial, protected, excluded


def score_intervals(selected, reference, duration):
    """Score a view against labeled ranges, masking uncertainty in every metric."""
    selected = union_intervals(selected, duration)
    commercial, protected, excluded = annotation_intervals(reference, duration)
    result = {
        "selected_seconds": _seconds(selected),
        "annotated_commercial_seconds": _seconds(commercial),
        "annotated_protected_seconds": _seconds(protected),
        "commercial_seconds_selected": _seconds(_intersection(selected, commercial)),
        "protected_seconds_selected": _seconds(_intersection(selected, protected)),
        "unverified_seconds_selected": _seconds(_intersection(selected, excluded)),
    }
    result["commercial_seconds_missed"] = max(0., result["annotated_commercial_seconds"] - result["commercial_seconds_selected"])
    result["unlabeled_seconds_selected"] = max(0., result["selected_seconds"] - result["commercial_seconds_selected"] - result["protected_seconds_selected"] - result["unverified_seconds_selected"])
    for category in ("commercial", "protected"):
        units = []
        for index, unit in enumerate(reference[category + "_units"]):
            eligible = _subtract(union_intervals([unit], duration), excluded)
            total = _seconds(eligible)
            if total <= 0:
                continue
            covered = _seconds(_intersection(selected, eligible))
            units.append({"index": index, "annotated_seconds": total, "selected_seconds": covered,
                          "coverage": covered / total, "any_selected": covered > 1e-7,
                          "fully_selected": math.isclose(covered, total, abs_tol=1e-7, rel_tol=0)})
        result[category + "_units"] = units
        result[category + "_unit_count"] = len(units)
        result[category + "_units_any_selected"] = sum(unit["any_selected"] for unit in units)
        result[category + "_units_fully_selected"] = sum(unit["fully_selected"] for unit in units)
    return result


def score_cuts(cuts, reference, duration, *, backend="verified-ai"):
    views = {
        "candidates": cuts,
        "verified": [cut for cut in cuts if cut.get("label") == "commercial"
                     and not cut.get("requires_review", True) and cut.get("confidence", 0) >= .90],
        "approved": [cut for cut in cuts if cut.get("approved") is True],
    }
    return {name: None if name == "verified" and backend == "kev" else score_intervals(selected, reference, duration)
            for name, selected in views.items()}


def _code_hashes():
    files = ("castwell/processing.py", "castwell/ad_review.py", "castwell/jev.py", "castwell/evaluation.py", "castwell/transcript_quality.py",
             "castwell/recording_evaluation.py", "scripts/evaluate_recordings.py")
    return {name: sha256(ROOT / name) for name in files}


def _classifier_environment():
    return {key: os.environ.get(key, default) for key, default in (
        ("CASTWELL_AI_WINDOW_CHARS", "18000"), ("CASTWELL_AI_CONTEXT_SEGMENTS", "12"), ("CASTWELL_AI_TIMEOUT", "180"))}


def _public_url(value):
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _load_reference(path):
    path = Path(path).resolve()
    reference = json.loads(path.read_text(encoding="utf-8"))
    if reference.get("detector_predictions_seen") is not False:
        raise ValueError("Reference must explicitly declare no detector predictions seen")
    transcript_path = Path(reference["transcript"])
    if not transcript_path.is_absolute():
        transcript_path = path.parent / transcript_path
    transcript_hash = sha256(transcript_path)
    if transcript_hash != reference["transcript_sha256"]:
        raise ValueError("Reference transcript hash changed")
    transcript = processing.validate_transcript(json.loads(transcript_path.read_text(encoding="utf-8")))
    # Validation intentionally does not relabel, extend, or infer annotations.
    annotation_intervals(reference, transcript["duration"])
    if not isinstance(reference.get("commercial_units"), list) or not isinstance(reference.get("protected_units"), list):
        raise ValueError("Reference units must be lists")
    manifest_path = path.parent / "source-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Recovery copies retain the original clip stem; callers may set audio_path
    # explicitly when importing references from another layout.
    stem = transcript_path.name.replace(".transcript.json", "").replace(".recovery-candidate.json", "")
    audio_path = Path(reference.get("audio_path", path.parent / (stem + ".wav")))
    if not audio_path.is_absolute():
        audio_path = path.parent / audio_path
    clip = next((row for row in manifest["clips"] if Path(row["path"]).resolve() == audio_path.resolve()), None)
    if clip is None or sha256(audio_path) != clip["sha256"]:
        raise ValueError("Audio clip missing from manifest or hash changed")
    source_stem = stem.removesuffix(".first-10m").removesuffix(".last-10m")
    metadata_path = path.parent / (source_stem + ".metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    source_audio = Path(metadata["local_path"])
    if sha256(source_audio) != metadata["sha256"]:
        raise ValueError("Original downloaded audio hash changed")
    provenance = {
        "reference_file": path.name, "reference_sha256": sha256(path),
        "transcript_file": transcript_path.name, "transcript_sha256": transcript_hash,
        "clip_file": audio_path.name, "clip_sha256": clip["sha256"],
        "source_audio_sha256": metadata["sha256"],
        "source_offset_seconds": clip["source_offset_seconds"],
        "source_episode_url": _public_url(metadata.get("episode_page")),
        "source_feed_url": _public_url(manifest.get("feeds", {}).get(source_stem.split("-")[0], {}).get("url")),
        "downloaded_at_utc": metadata["downloaded_at_utc"],
        "source_manifest_sha256": sha256(manifest_path),
        "metadata_sha256": sha256(metadata_path),
        "duration": transcript["duration"],
    }
    return path, reference, transcript, provenance


def public_summary(report):
    """Strict allowlist: no cut reasons, model evidence, transcripts or errors."""
    output = {key: report[key] for key in ("schema_version", "started_at_utc", "configuration", "provenance", "limitations")}
    output["clips"] = []
    for row in report["clips"]:
        clean = {key: row[key] for key in ("id", "status", "provenance", "eligible", "elapsed_seconds")}
        if row.get("metrics") is not None:
            clean["metrics"] = row["metrics"]
        if row["status"] == "error":
            clean["error_type"] = row["error_type"]
        output["clips"].append(clean)
    successful = [row for row in report["clips"] if row["status"] == "ok"]
    output["summary"] = {
        "total_clips": len(report["clips"]), "successful_clips": len(successful),
        "failed_clips": sum(row["status"] == "error" for row in report["clips"]),
        "unfinished_clips": sum(row["status"] in {"pending", "running"} for row in report["clips"]),
        "eligible_commercial_seconds_including_failed_and_unfinished": sum(row["eligible"]["commercial_seconds"] for row in report["clips"]),
        "eligible_protected_seconds_including_failed_and_unfinished": sum(row["eligible"]["protected_seconds"] for row in report["clips"]),
        "elapsed_seconds": sum(row["elapsed_seconds"] for row in report["clips"]),
        "views": {},
    }
    fields = ("selected_seconds", "annotated_commercial_seconds", "annotated_protected_seconds",
              "commercial_seconds_selected", "commercial_seconds_missed", "protected_seconds_selected",
              "unverified_seconds_selected", "unlabeled_seconds_selected", "commercial_unit_count",
              "commercial_units_any_selected", "commercial_units_fully_selected", "protected_unit_count",
              "protected_units_any_selected", "protected_units_fully_selected")
    for view in VIEWS:
        if not report["configuration"].get("view_availability", {}).get(view, True):
            output["summary"]["views"][view] = None
            continue
        output["summary"]["views"][view] = {key: sum(row["metrics"][view][key] for row in successful) for key in fields}
    return output


def _save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def evaluate_recordings(references, *, output, summary_output, base_url, model, model_label,
                        model_files, candidate_config=None, resume=False, on_progress=None,
                        detector=None, backend="verified-ai"):
    """Run an explicit local verified-AI or Kev backend, with review enabled."""
    if backend not in {"verified-ai", "kev"}:
        raise ValueError("Recording backend must be verified-ai or kev")
    base_url = validate_endpoint(base_url)  # No remote or paid override exists.
    if backend == "kev":
        from . import jev
        jev._local_endpoint(base_url)  # Validate the stricter typed endpoint path before any work.
    if not model or not model_label or not model_files:
        raise ValueError("Model alias, exact label and at least one model artifact are required")
    output, summary_output = Path(output).resolve(), Path(summary_output).resolve()
    if output == summary_output:
        raise ValueError("Private output and public summary must use different files")
    if output.exists() and not resume:
        raise ValueError("Output already exists; use --resume to continue matching checkpoints")
    inputs = [_load_reference(path) for path in references]
    if not inputs or len({row[0] for row in inputs}) != len(inputs):
        raise ValueError("Supply distinct reference files")
    protected_paths = {row[0] for row in inputs}
    protected_paths.update(Path(row[1]["transcript"]).resolve() for row in inputs)
    protected_paths.update(Path(path).resolve() for path in model_files)
    if output in protected_paths or summary_output in protected_paths:
        raise ValueError("Reports cannot replace an input artifact")
    hashes = _code_hashes()
    provenance = {"source_sha256": hashes, "model_artifacts": [
        {"file": Path(path).name, "sha256": sha256(path), "bytes": Path(path).stat().st_size} for path in model_files]}
    if candidate_config:
        candidate = json.loads(Path(candidate_config).read_text(encoding="utf-8"))
        for name, expected in candidate.get("source_sha256", {}).items():
            matching = next((actual for key, actual in hashes.items() if Path(key).name == Path(name).name), None)
            if matching is not None and matching != expected:
                raise ValueError("Source differs from frozen candidate configuration")
        provenance["candidate_configuration_sha256"] = sha256(candidate_config)
    environment = _classifier_environment() if backend == "verified-ai" else {}
    configuration = {"backend": backend, "base_url": base_url, "model": model,
                     "model_label": model_label, "review_only": True,
                     "auto_approve_threshold": .90 if backend == "verified-ai" else None,
                     "transport": "literal loopback, no redirects, no inherited credentials or proxies",
                     "classifier_environment": environment,
                     "view_availability": {"candidates": True, "verified": backend == "verified-ai", "approved": True},
                     "confidence_semantics": {
                         "confidence": "normalized_probability_margin", "review_score": "selected_probability",
                         "review_threshold": .90,
                         "interpretation": "Selected-class probability is separate from the normalized confidence margin. Neither is established real-recording accuracy; scores are not interchangeable with verified AI."
                     } if backend == "kev" else {
                         "confidence": "minimum_model_self_report_across_two_passes", "review_score": "confidence",
                         "review_threshold": .90,
                         "interpretation": "Self-reported confidence is not a calibrated probability; approval also requires intent/boundary agreement and alignment safeguards."
                     }}
    backend_limit = ("Kev has no two-pass intent/boundary verifier. Its verified view is unavailable (null), not a failed quality score. All Kev cuts require manual approval."
                     if backend == "kev" else "Verified suggestions passed both model checks and alignment safeguards; actual approval remains off during this run.")
    report = {"schema_version": 1, "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "configuration": configuration, "provenance": provenance, "limitations": [*LIMITATIONS, backend_limit], "clips": []}
    for path, reference, transcript, identity in inputs:
        commercial, protected, excluded = annotation_intervals(reference, transcript["duration"])
        report["clips"].append({"id": path.name, "status": "pending", "provenance": identity,
            "eligible": {"commercial_seconds": _seconds(commercial), "protected_seconds": _seconds(protected),
                         "unverified_seconds": _seconds(excluded)}, "elapsed_seconds": 0.})
    if resume and output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if (previous["configuration"] != configuration or previous["provenance"] != provenance
                or [row["provenance"] for row in previous["clips"]] != [row["provenance"] for row in report["clips"]]):
            raise ValueError("Checkpoint inputs or candidate changed; choose a new output")
        report = previous
    def checkpoint():
        _save(output, report)
        _save(summary_output, public_summary(report))
    checkpoint()
    run = detector or (jev.classify_local_transcript if backend == "kev" else processing.detect_ads)
    for row, (_, reference, transcript, _) in zip(report["clips"], inputs):
        if row["status"] in {"ok", "error"}:
            continue
        current_environment = _classifier_environment() if backend == "verified-ai" else {}
        if _code_hashes() != hashes or current_environment != environment:
            raise ValueError("Candidate code or classifier environment changed during evaluation")
        row["status"] = "running"
        checkpoint()
        started = time.monotonic()
        try:
            if backend == "kev":
                if on_progress:
                    on_progress(row["id"], "Checking typed commercial roles with local Kev")
                provider_result = run(transcript, base_url=base_url, model=model)
                row["provider_result"] = provider_result
                cuts = provider_result["cuts"]
            else:
                cuts = run(transcript, "ai", config={"ai_base_url": base_url, "ai_model": model,
                    "ai_key": "", "ai_allow_redirects": False, "ai_trust_env": False,
                    "ai_policy": "verified", "review_only": True, "auto_approve_threshold": .90},
                    progress=(lambda message: on_progress(row["id"], message)) if on_progress else None)
            row["cuts"] = processing.validate_cuts(cuts, transcript["duration"])
            row["metrics"] = score_cuts(row["cuts"], reference, transcript["duration"], backend=backend)
            row["status"] = "ok"
        except Exception as exc:
            row.update(status="error", error_type=type(exc).__name__, error=str(exc), metrics=None)
        finally:
            row["elapsed_seconds"] = time.monotonic() - started
            checkpoint()
        if on_progress:
            on_progress(row["id"], row["status"])
    return public_summary(report)
