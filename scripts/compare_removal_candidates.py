#!/usr/bin/env python3
"""Compare saved removal candidates without inference, audio edits or approvals.

Input: --benchmark ID=REPORT (repeatable) imports speed_benchmark reports.
Alternatively --manifest reads {"schema_version": 1, "candidates": [...]}.
Each normalized candidate has id, run_status, score_scope, source_audio_sha256,
reference_sha256, audio_seconds, timing, evidence, word_metrics and elapsed_audio_metrics.
See from_benchmark() for that schema. Missing evidence remains unknown.

The primary accuracy metric is elapsed annotated audio, not summed ASR word
duration. Existing word-only reports therefore cannot produce a verified pass.
score_elapsed_intervals() supports independently supplied regions and separate
sponsor-read and podcast-promo inventories; it does not label transcript gaps.

Default thresholds: 5 remaining ad seconds per audio hour, 300 processing
seconds per audio hour, and separate 0/1/3/5 show-loss seconds PER EPISODE.
Exploratory columns are comparisons only and never change application defaults.
All candidates must use identical source/reference hashes, duration and explicit
removal scope. Historical sponsor-only comparisons never establish current-scope
acceptance. Current acceptance requires complete sponsor and podcast-promo
inventories; absent podcast-promo metadata remains unknown.
Outputs contain allowlisted aggregates, identities and reasons, never raw input
reports, transcript text or filesystem paths. Evidence declarations are trusted
input claims, not independent attestations by this offline tool.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from castwell.ad_scope import REMOVAL_SCOPE

SCOPE = REMOVAL_SCOPE
HISTORICAL_SCOPE = "sponsor-product-reads-only-v1"
SUPPORTED_SCOPES = frozenset((SCOPE, HISTORICAL_SCOPE))
EPSILON = 1e-7


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default errors echo unknown options and invalid values.
        self.exit(2, json.dumps({"status": "failed", "error": "Invalid command-line arguments; values were not echoed."}) + "\n")


def _number(value, *, positive=False):
    try:
        valid = (type(value) in (int, float) and math.isfinite(value)
                 and (value > 0 if positive else value >= 0))
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("Expected a finite positive or nonnegative number")
    return float(value)


def _flag(value):
    if value is not None and type(value) is not bool:
        raise ValueError("Evidence flags must be boolean or null")
    return value


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    return value


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Expected a lowercase SHA256 identity")
    return value


def _scope(value):
    if not isinstance(value, str) or value not in SUPPORTED_SCOPES:
        raise ValueError("Candidates must declare a supported explicit removal scope")
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
        raise ValueError("Candidate IDs must be short letters, numbers, dots, hyphens or underscores")
    if value.lower().startswith(("apikey", "sk-")):
        raise ValueError("Candidate IDs must not contain credentials")
    return value


def _intervals(rows, duration):
    if not isinstance(rows, list):
        raise ValueError("Intervals must be a list")
    result = []
    for row in rows:
        row = _object(row)
        start, end = _number(row["start"]), _number(row["end"])
        if not start < end <= duration:
            raise ValueError("Intervals must be positive and source-bounded")
        result.append((start, end))
    return sorted(result)


def _union(rows):
    result = []
    for start, end in sorted(rows):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def _seconds(rows):
    return sum(end - start for start, end in rows)


def _intersection_seconds(left, right):
    return sum(max(0., min(b, d) - max(a, c)) for a, b in left for c, d in right)


def score_elapsed_intervals(cuts, reference, audio_seconds):
    """Score explicit audio regions without treating unlabeled time as show.

    reference: {regions: [{start, end, label}], sponsor_reads: [{start, end}],
                sponsor_read_inventory_complete: bool|null,
                podcast_promos: [{start, end}]|null,
                podcast_promo_inventory_complete: bool|null}.
    Region labels are commercial, protected or unknown; regions cannot overlap.
    Missing regions count as unknown. A declared complete sponsor-read inventory
    must exactly cover commercial regions outside the separately declared promo
    ranges. Both inventories contain only commercial audio and cannot overlap.
    Complete promo coverage requires an explicit list, empty when absent; missing
    promo metadata stays unknown and cannot establish current-policy acceptance.
    Silence inside a labeled ad region counts as elapsed ad time. It is never
    inferred from word endpoints. No word metadata or text is accepted as truth.
    """
    duration = _number(audio_seconds, positive=True)
    reference = _object(reference)
    regions = reference["regions"]
    region_spans = _intervals(regions, duration)
    if any(left[1] > right[0] for left, right in zip(region_spans, region_spans[1:])):
        raise ValueError("Reference audio regions cannot overlap")
    if any(row.get("label") not in {"commercial", "protected", "unknown"} for row in regions):
        raise ValueError("Reference audio regions require known labels")
    commercial = _union(_intervals([r for r in regions if r["label"] == "commercial"], duration))
    protected = _union(_intervals([r for r in regions if r["label"] == "protected"], duration))
    selected = _union(_intervals(cuts, duration))
    ad_selected = _intersection_seconds(selected, commercial)
    show_selected = _intersection_seconds(selected, protected)
    reads = reference.get("sponsor_reads")
    inventory_complete = _flag(reference.get("sponsor_read_inventory_complete"))
    whole_missed = None
    if reads is not None:
        reads = _intervals(reads, duration)
        if any(a[1] > b[0] for a, b in zip(reads, reads[1:])):
            raise ValueError("Sponsor reads cannot overlap")
        if any(abs((b - a) - _intersection_seconds([(a, b)], commercial)) > EPSILON for a, b in reads):
            raise ValueError("Sponsor reads must contain only labeled commercial audio")
        whole_missed = sum(_intersection_seconds([read], selected) <= EPSILON for read in reads)
    promos = reference.get("podcast_promos")
    promo_inventory_complete = _flag(reference.get("podcast_promo_inventory_complete"))
    whole_promos_missed = None
    if promos is not None:
        promos = _intervals(promos, duration)
        if any(a[1] > b[0] for a, b in zip(promos, promos[1:])):
            raise ValueError("Podcast promos cannot overlap")
        if any(abs((b - a) - _intersection_seconds([(a, b)], commercial)) > EPSILON for a, b in promos):
            raise ValueError("Podcast promos must contain only labeled commercial audio")
        if reads is not None and _intersection_seconds(reads, promos) > EPSILON:
            raise ValueError("Sponsor and podcast-promo inventories cannot overlap")
        whole_promos_missed = sum(_intersection_seconds([promo], selected) <= EPSILON for promo in promos)
    if promo_inventory_complete is True and promos is None:
        raise ValueError("Complete podcast-promo inventory requires explicit units, including an empty list when absent")
    expected_sponsor_seconds = _seconds(commercial) - (0. if promos is None else _seconds(promos))
    if inventory_complete is True and (reads is None or abs(_seconds(reads) - expected_sponsor_seconds) > EPSILON):
        raise ValueError("Complete sponsor-read inventory must cover every commercial region")
    return {
        "commercial_missed_seconds": max(0., _seconds(commercial) - ad_selected),
        "protected_selected_seconds": show_selected,
        "unknown_audio_seconds": max(0., duration - _seconds(commercial) - _seconds(protected)),
        "unknown_audio_selected_seconds": max(0., _seconds(selected) - ad_selected - show_selected),
        "whole_sponsor_reads_missed": whole_missed,
        "sponsor_read_inventory_complete": inventory_complete,
        "whole_podcast_promos_missed": whole_promos_missed,
        "podcast_promo_inventory_complete": promo_inventory_complete,
    }


def _rendered_score(report):
    if report.get("status") != "complete" or report.get("scores") is None:
        return None
    scores = _object(report["scores"])
    return None if scores.get("rendered") is None else _object(scores["rendered"])


def from_benchmark(report, candidate_id):
    """Import existing full benchmark reports, retaining word-only limitations.

    Never promote ASR word durations or an absent unknown-gap list into elapsed
    audio coverage. Sponsor-read absence cannot be established from aggregate
    word scores. Only rendered-output scores describe the resulting audio;
    proposal-only reports remain unknown even when proposals have good scores.
    Probe/partial timings and incomplete request/download accounting cannot pass.
    """
    report = _object(report)
    provenance = _object(report["reference"])
    timing = _object(report.get("timing", {}))
    runtime = _object(report.get("runtime_evidence", {}))
    score = _rendered_score(report)
    word_metrics = None
    if score is not None:
        categories = _object(score["categories"])
        commercial, protected = categories["commercial"], categories["protected"]
        word_metrics = {
            "commercial_missed_seconds": commercial["word_seconds_unselected"],
            "protected_selected_seconds": protected["word_seconds_selected"],
            "zero_duration_commercial_points_missed": commercial["zero_duration_word_entries"]
                                                       - commercial["zero_duration_points_selected"],
            "zero_duration_protected_points_selected": protected["zero_duration_points_selected"],
        }
    source = report.get("status") == "complete"
    uncached = timing.get("no_transcript_or_prediction_reuse") is True
    empty_cache = lambda name: type(runtime.get(name)) is int and runtime[name] == 0
    return {
        "id": candidate_id,
        "run_status": report["status"] if report.get("status") in {"complete", "failed"} else "incomplete",
        "score_scope": "rendered" if score is not None else "unknown",
        "render_mode": report["render_mode"] if report.get("render_mode") in {"candidate_preview", "approved_cuts"} else "unknown",
        "source_audio_sha256": provenance["source_audio_sha256"],
        "reference_sha256": provenance["reference_sha256"],
        "audio_seconds": report["audio_seconds"],
        "timing": {
            "wall_seconds": report.get("elapsed_seconds"),
            "complete_pipeline": source and timing.get("complete_pipeline") is True,
            "fresh_transcription": source and uncached and report.get("transcript_reused") is False
                                   and empty_cache("transcript_cache_hits"),
            "fresh_predictions": source and uncached and empty_cache("prediction_cache_hits"),
            "cold_models": runtime.get("models_cold_at_start"),
            "cuda_only": runtime.get("cuda_only_verified"),
            "request_telemetry_complete": runtime.get("request_telemetry_complete"),
            "asset_downloads": runtime.get("asset_downloads"),
        },
        "evidence": {
            "removal_scope": provenance.get("removal_scope"),
            "consumed_development": provenance.get("consumed_development"),
            "human_listening_verified": provenance.get("human_listening_verified_declaration"),
            "payment_status_verified": provenance.get("payment_verified_declaration"),
            "acceptance_eligible": provenance.get("acceptance_eligible"),
        },
        "word_metrics": word_metrics,
        "elapsed_audio_metrics": None,
    }


def protected_controls_from_reference(report, reference, reference_sha256):
    """Summarize existing parody word labels, not payment status or elapsed audio.

    Requires the exact frozen reference used by the report. No control is scored
    when analysis failed or protected error rows are unavailable. This supports
    the historical full-episode-word-ranges reference with explicit protected
    roles; it does not identify parody by searching transcript text.
    """
    provenance = _object(report["reference"])
    if (_hash(reference_sha256) != provenance["reference_sha256"]
            or reference.get("source_sha256") != provenance["source_audio_sha256"]
            or _scope(reference.get("removal_scope")) != _scope(provenance.get("removal_scope"))):
        raise ValueError("Control reference must match the report's frozen source and reference identity")
    units = reference.get("protected")
    if not isinstance(units, list):
        raise ValueError("Control reference requires explicit protected unit roles")
    score = _rendered_score(report)
    errors = None if score is None else score.get("errors", {}).get("protected_selected")
    if errors is not None and not isinstance(errors, list):
        raise ValueError("Protected errors must be a list or unknown")
    controls = []
    for unit in units:
        if unit.get("role") != "unpaid_parody":
            continue
        first, last = unit["global_word_index_range_inclusive"]
        if type(first) is not int or type(last) is not int or not 0 <= first <= last:
            raise ValueError("Control word locators must be valid inclusive IDs")
        control = {"id": _identifier(unit["id"]), "reference_role": "reference_labeled_parody",
                   "reference_word_entries": last - first + 1,
                   "reference_word_seconds": _number(unit["word_seconds"]),
                   "selected_word_entries": None, "selected_word_seconds": None,
                   "zero_duration_points_selected": None}
        if errors is not None:
            chosen, identities = [], set()
            for error in errors:
                identifier = error["global_word_index"]
                if type(identifier) is not int or identifier < 0 or identifier in identities:
                    raise ValueError("Protected error word identities must be unique integers")
                identities.add(identifier)
                if first <= identifier <= last:
                    selected = _number(error["selected_seconds"])
                    start, end = _number(error["start"]), _number(error["end"])
                    if end < start or selected > end - start + EPSILON:
                        raise ValueError("Protected error duration is invalid")
                    chosen.append((selected, end == start))
            control.update(selected_word_entries=len(chosen), selected_word_seconds=sum(x[0] for x in chosen),
                           zero_duration_points_selected=sum(x[1] for x in chosen))
            if control["selected_word_seconds"] > control["reference_word_seconds"] + EPSILON:
                raise ValueError("Protected control exceeds its frozen reference duration")
        controls.append(control)
    return controls


def _controls(value):
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("Protected controls must be a list")
    rows = []
    for item in value:
        item = _object(item)
        if item.get("reference_role") != "reference_labeled_parody":
            raise ValueError("Control must retain its reference-labeled role")
        row = {"id": _identifier(item["id"]), "reference_role": "reference_labeled_parody"}
        for key in ("reference_word_entries", "selected_word_entries", "zero_duration_points_selected"):
            count = item.get(key)
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("Protected control counts must be nonnegative integers or null")
            row[key] = count
        for key in ("reference_word_seconds", "selected_word_seconds"):
            row[key] = None if item.get(key) is None else _number(item[key])
        rows.append(row)
    return rows


def _metrics(value, *, elapsed, duration):
    if value is None:
        return None
    value = _object(value)
    result = {name: _number(value[name]) for name in ("commercial_missed_seconds", "protected_selected_seconds")}
    if elapsed:
        for key in ("unknown_audio_seconds", "unknown_audio_selected_seconds"):
            result[key] = None if value.get(key) is None else _number(value[key])
        for key in ("commercial_missed_seconds", "protected_selected_seconds", "unknown_audio_seconds"):
            if result[key] is not None and result[key] > duration + EPSILON:
                raise ValueError("Elapsed audio metrics cannot exceed source duration")
        if sum(result[k] for k in ("commercial_missed_seconds", "protected_selected_seconds")) > duration + EPSILON:
            raise ValueError("Disjoint elapsed audio errors cannot exceed source duration")
        if (result["unknown_audio_seconds"] is not None and sum(result[k] for k in (
                "commercial_missed_seconds", "protected_selected_seconds", "unknown_audio_seconds")) > duration + EPSILON):
            raise ValueError("Disjoint errors and unknown coverage cannot exceed source duration")
        if (result["unknown_audio_selected_seconds"] is not None and result["unknown_audio_seconds"] is not None
                and result["unknown_audio_selected_seconds"] > result["unknown_audio_seconds"] + EPSILON):
            raise ValueError("Selected unknown duration exceeds unknown coverage")
        count = value.get("whole_sponsor_reads_missed")
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError("Whole missed read count must be a nonnegative integer or null")
        result["whole_sponsor_reads_missed"] = count
        result["sponsor_read_inventory_complete"] = _flag(value.get("sponsor_read_inventory_complete"))
        promos = value.get("whole_podcast_promos_missed")
        if promos is not None and (type(promos) is not int or promos < 0):
            raise ValueError("Whole missed promo count must be a nonnegative integer or null")
        result["whole_podcast_promos_missed"] = promos
        result["podcast_promo_inventory_complete"] = _flag(value.get("podcast_promo_inventory_complete"))
    else:
        for key in ("zero_duration_commercial_points_missed", "zero_duration_protected_points_selected"):
            count = value.get(key)
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("Point counts must be nonnegative integers or null")
            result[key] = count
    return result


def _candidate(value):
    value = _object(value)
    duration = _number(value["audio_seconds"], positive=True)
    timing, evidence = _object(value["timing"]), _object(value["evidence"])
    if value.get("run_status") not in {"complete", "failed", "incomplete"}:
        raise ValueError("Candidate requires explicit complete, failed or incomplete status")
    if value.get("score_scope") not in {"rendered", "proposed", "unknown"}:
        raise ValueError("Candidate requires explicit rendered, proposed or unknown score scope")
    scope = _scope(evidence.get("removal_scope"))
    result = {
        "id": _identifier(value["id"]), "run_status": value["run_status"], "score_scope": value["score_scope"],
        "render_mode": value.get("render_mode", "unknown"),
        "source_audio_sha256": _hash(value["source_audio_sha256"]),
        "reference_sha256": _hash(value["reference_sha256"]), "audio_seconds": duration,
        "timing": {name: _flag(timing.get(name)) for name in (
            "complete_pipeline", "fresh_transcription", "fresh_predictions", "cold_models", "cuda_only", "request_telemetry_complete")},
        "evidence": {name: _flag(evidence.get(name)) for name in (
            "consumed_development", "human_listening_verified", "payment_status_verified", "acceptance_eligible")},
        "word_metrics": _metrics(value.get("word_metrics"), elapsed=False, duration=duration),
        "elapsed_audio_metrics": _metrics(value.get("elapsed_audio_metrics"), elapsed=True, duration=duration),
        "protected_controls": _controls(value.get("protected_controls")),
    }
    if result["render_mode"] not in {"candidate_preview", "approved_cuts", "unknown"}:
        raise ValueError("Render mode must be a known mode or unknown")
    downloads = timing.get("asset_downloads")
    if downloads is not None and (type(downloads) is not int or downloads < 0):
        raise ValueError("Asset downloads must be a nonnegative integer or null")
    result["timing"]["asset_downloads"] = downloads
    result["timing"]["wall_seconds"] = None if timing.get("wall_seconds") is None else _number(timing["wall_seconds"])
    if (value["run_status"] == "complete" and result["timing"]["complete_pipeline"] is True
            and result["timing"]["wall_seconds"] == 0):
        raise ValueError("A completed audio pipeline requires positive elapsed wall time")
    result["evidence"]["removal_scope"] = scope
    if value.get("benchmark_report_sha256") is not None:
        result["benchmark_report_sha256"] = _hash(value["benchmark_report_sha256"])
    return result


def _thresholds(metrics, ad_budget, show_budget):
    if metrics is None:
        return None
    return {
        "remaining_ads_within_budget": metrics["commercial_missed_seconds"] <= ad_budget + EPSILON,
        "show_loss_within_budget": metrics["protected_selected_seconds"] <= show_budget + EPSILON,
    }


def compare_candidates(candidates, *, max_ad_seconds_per_hour=5., max_processing_seconds_per_hour=300.,
                       show_loss_budgets=(0., 1., 3., 5.)):
    max_ads = _number(max_ad_seconds_per_hour)
    max_time = _number(max_processing_seconds_per_hour, positive=True)
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("At least one candidate is required")
    if not isinstance(show_loss_budgets, (tuple, list)) or not show_loss_budgets:
        raise ValueError("At least one explicit show-loss budget is required")
    budgets = sorted(set(_number(value) for value in show_loss_budgets))
    rows = [_candidate(value) for value in candidates]
    identities = {(r["source_audio_sha256"], r["reference_sha256"], r["audio_seconds"], r["evidence"]["removal_scope"]) for r in rows}
    if len(identities) != 1 or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Use unique candidate IDs and matching source/reference hashes, duration and removal scope")
    for row in rows:
        duration, timing = row["audio_seconds"], row["timing"]
        ad_budget = max_ads * duration / 3600
        elapsed = timing["wall_seconds"]
        timing_eligible = row["run_status"] == "complete" and timing["asset_downloads"] == 0 and all(timing[k] is True for k in (
            "complete_pipeline", "fresh_transcription", "fresh_predictions", "cold_models", "cuda_only", "request_telemetry_complete"))
        normalized = None if elapsed is None else elapsed * 3600 / duration
        row["timing"]["observed_seconds_per_audio_hour"] = normalized
        row["timing"]["eligible_for_speed_gate"] = timing_eligible and elapsed is not None
        row["timing"]["speed_target_met"] = None if not row["timing"]["eligible_for_speed_gate"] else normalized <= max_time + EPSILON
        for key in ("word_metrics", "elapsed_audio_metrics"):
            if row[key] is not None:
                row[key]["commercial_missed_seconds_per_audio_hour"] = row[key]["commercial_missed_seconds"] * 3600 / duration
        reasons = []
        if row["run_status"] != "complete":
            reasons.append("analysis_failed_or_incomplete")
        if row["score_scope"] != "rendered":
            reasons.append("rendered_output_score_not_established")
        if not row["timing"]["eligible_for_speed_gate"]:
            reasons.append("fresh_complete_cold_cuda_timing_not_established")
        evidence = row["evidence"]
        current_scope = evidence["removal_scope"] == SCOPE
        if not current_scope:
            reasons.append("historical_removal_scope_not_current_acceptance")
        if evidence["human_listening_verified"] is not True:
            reasons.append("human_listening_not_verified")
        if evidence["consumed_development"] is not False:
            reasons.append("reference_consumed_or_consumption_unknown")
        if evidence["acceptance_eligible"] is not True:
            reasons.append("reference_not_declared_acceptance_eligible")
        audio = row["elapsed_audio_metrics"]
        if audio is None:
            reasons += ["elapsed_audio_accuracy_unknown", "complete_sponsor_read_coverage_unknown"]
            if current_scope:
                reasons.append("complete_podcast_promo_coverage_unknown")
        else:
            if audio["unknown_audio_seconds"] is None or audio["unknown_audio_seconds"] > EPSILON:
                reasons.append("audio_reference_coverage_incomplete_or_unknown")
            if audio["sponsor_read_inventory_complete"] is not True or audio["whole_sponsor_reads_missed"] is None:
                reasons.append("complete_sponsor_read_coverage_unknown")
            if current_scope and (audio["podcast_promo_inventory_complete"] is not True
                                  or audio["whole_podcast_promos_missed"] is None):
                reasons.append("complete_podcast_promo_coverage_unknown")
        row["verification_blockers"] = reasons
        row["threshold_matrix"] = []
        for budget in budgets:
            word = _thresholds(row["word_metrics"], ad_budget, budget)
            elapsed_checks = _thresholds(audio, ad_budget, budget)
            whole_missed = None if audio is None else audio["whole_sponsor_reads_missed"]
            promos_missed = None if audio is None or not current_scope else audio["whole_podcast_promos_missed"]
            known_failure = (row["run_status"] == "failed" or row["timing"]["speed_target_met"] is False
                or (elapsed_checks is not None and not all(elapsed_checks.values()))
                or (whole_missed is not None and whole_missed > 0)
                or (promos_missed is not None and promos_missed > 0))
            verified = not reasons and not known_failure
            row["threshold_matrix"].append({
                "show_loss_budget_seconds_per_episode": budget, "ad_budget_seconds_per_episode": ad_budget,
                "word_proxy_checks": word, "elapsed_audio_checks": elapsed_checks,
                "word_proxy_thresholds_met": None if word is None else all(word.values()),
                "no_whole_sponsor_read_missed": None if whole_missed is None else whole_missed == 0,
                "no_whole_podcast_promo_missed": None if promos_missed is None else promos_missed == 0,
                "result": "run_failed" if row["run_status"] == "failed" else "fails_declared_thresholds" if known_failure else "verified_pass" if verified else "not_verified",
            })
    first = rows[0]
    return {
        "schema_version": 1, "removal_scope": first["evidence"]["removal_scope"],
        "current_removal_scope": SCOPE,
        "scope_matches_current_policy": first["evidence"]["removal_scope"] == SCOPE,
        "comparison_identity": {key: first[key] for key in ("source_audio_sha256", "reference_sha256", "audio_seconds")},
        "thresholds": {"max_ad_seconds_per_audio_hour": max_ads,
                       "max_processing_seconds_per_audio_hour": max_time,
                       "show_loss_budgets_seconds_per_episode": budgets,
                       "whole_missed_sponsor_reads_allowed": 0},
        "candidates": rows, "application_defaults_changed": False, "inference_requests": 0,
        "limitations": [
            "Elapsed-audio errors and summed ASR word-duration proxies are distinct and are never substituted.",
            "Unknown or unlistened coverage cannot establish a verified pass; consumed references remain development evidence.",
            "A verified pass is conditional on supplied metadata and reviewed reference declarations, not an independent attestation.",
            "A whole missed sponsor read fails regardless of its duration or the remaining-ad budget.",
            "Current-scope acceptance also requires an explicit complete podcast-promo inventory; a whole missed genuine promo fails regardless of duration.",
            "Matching historical sponsor-only cohorts remain comparable development evidence, never acceptance for the current removal scope.",
            "Show-loss columns are seconds per episode; ad and processing budgets scale by full source audio duration.",
            "Cached or partial timings cannot pass the fresh complete cold CUDA speed gate.",
            "Incomplete request telemetry or unknown/nonzero asset downloads cannot pass the speed gate; proposal scores do not describe an approved-subset export.",
            "No inferred silence labels, reference expansion, model calls, application approvals or cut edits occur.",
        ],
    }


def main(argv=None):
    parser = SafeArgumentParser(prog="compare_removal_candidates", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--benchmark", action="append", default=[], metavar="ID=REPORT")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--control-reference", type=Path, help="Exact frozen word-range reference for protected parody aggregates")
    parser.add_argument("--max-ad-seconds-per-hour", type=float, default=5.)
    parser.add_argument("--max-processing-seconds-per-hour", type=float, default=300.)
    parser.add_argument("--show-loss-seconds", type=float, nargs="+", default=[0., 1., 3., 5.])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    candidates = []
    control_reference, control_hash = None, None
    if args.control_reference:
        contents = args.control_reference.read_bytes()
        control_reference, control_hash = json.loads(contents.decode("utf-8-sig")), hashlib.sha256(contents).hexdigest()
    if args.manifest:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        if _object(manifest).get("schema_version") != 1 or not isinstance(manifest.get("candidates"), list):
            raise ValueError("Invalid comparison manifest schema")
        candidates.extend(manifest["candidates"])
    for item in args.benchmark:
        candidate_id, separator, path = item.partition("=")
        if not separator or not path:
            raise ValueError("Benchmark input must be ID=REPORT")
        contents = Path(path).read_bytes()
        report = json.loads(contents.decode("utf-8-sig"))
        candidate = from_benchmark(report, candidate_id)
        if control_reference is not None:
            candidate["protected_controls"] = protected_controls_from_reference(report, control_reference, control_hash)
        candidate["benchmark_report_sha256"] = hashlib.sha256(contents).hexdigest()
        candidates.append(candidate)
    result = compare_candidates(candidates, max_ad_seconds_per_hour=args.max_ad_seconds_per_hour,
        max_processing_seconds_per_hour=args.max_processing_seconds_per_hour, show_loss_budgets=args.show_loss_seconds)
    result["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(result, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        destination.write(payload)
    print(json.dumps({"status": "complete", "candidates": len(candidates), "inference_requests": 0}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyError, TypeError, ValueError, OverflowError, OSError, AttributeError):
        print(json.dumps({"status": "failed", "error": "Invalid, unmatched or unavailable comparison evidence; prior outputs were not overwritten."}))
        sys.exit(1)
