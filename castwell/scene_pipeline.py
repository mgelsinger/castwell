"""Compose scene discovery and endpoint review with inspectable approval proof.

The two classifiers retain their own fixed inference profiles. Automatic edits
require complete source-aligned evidence; a model score alone never approves an
edit. This module makes no requests other than through the composed stages.
"""

from __future__ import annotations

from copy import deepcopy
import math
from urllib.parse import urlsplit

from . import scene_edges, scene_review
from .ad_review import _review_units
from .processing import ProcessingError, _check_cancel, _words_text, validate_cuts, validate_transcript

POLICY_VERSION = "scene-pipeline-v3"


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _preflight(base_url, model, key, threshold, review_only, request_timeout,
               allow_redirects, trust_env, ai_reasoning, callbacks):
    if not isinstance(base_url, str) or not isinstance(model, str) or not model.strip():
        raise ValueError("Scene processing requires a valid local URL and model")
    parsed = urlsplit(base_url)
    try:
        parsed.port
    except ValueError:
        raise ValueError("Scene processing requires a valid loopback URL") from None
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or "?" in base_url or "#" in base_url or "\\" in base_url or any(char.isspace() for char in base_url)):
        raise ValueError("Scene processing uses a loopback model only")
    if key != "":
        raise ValueError("Local scene processing does not use an API key")
    if allow_redirects is not False or trust_env is not False:
        raise ValueError("Local scene processing requires redirects and environment proxies disabled")
    if not isinstance(review_only, bool) or not isinstance(ai_reasoning, bool):
        raise ValueError("Scene review and reasoning settings must be booleans")
    if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold) or not 0 <= threshold <= 1):
        raise ValueError("Scene approval threshold must be between zero and one")
    if (isinstance(request_timeout, bool) or not isinstance(request_timeout, (int, float))
            or not math.isfinite(request_timeout) or not 10 <= request_timeout <= 600):
        raise ValueError("Scene timeout must be between ten and six hundred seconds")
    if any(callback is not None and not callable(callback) for callback in callbacks):
        raise ValueError("Scene audit sinks must be callbacks")


def _capture(name):
    received = []
    def sink(audit):
        if received:
            raise ProcessingError(f"{name} published more than one audit")
        received.append(deepcopy(audit))
    return received, sink


def _scene_audit(audit, units):
    expected = {unit["id"] for unit in units}
    if (not isinstance(audit, dict) or set(audit) != expected
            or any(not _integer(identifier) for identifier in audit)):
        raise ProcessingError("Scene processing requires a complete matching scene audit")
    lookup = {unit["id"]: unit for unit in units}
    for identifier, row in audit.items():
        if (not isinstance(row, dict) or not _integer(row.get("segment_id")) or row["segment_id"] != identifier
                or not _integer(row.get("parent_segment_id"))
                or row["parent_segment_id"] != lookup[identifier]["parent_segment_id"]
                or not isinstance(row.get("boundary_audited"), bool)
                or row["boundary_audited"] != (row.get("boundary") is not None)):
            raise ProcessingError("Scene audit ownership or boundary coverage is invalid")
        for stage in ("intent", "boundary"):
            decision = row.get(stage)
            if stage == "boundary" and decision is None:
                continue
            if not isinstance(decision, dict):
                raise ProcessingError("Scene audit is missing a judgment")
            evidence, score, bounds = decision.get("evidence_id"), decision.get("confidence"), decision.get("source_range")
            if (not _integer(decision.get("id")) or decision["id"] != identifier
                    or decision.get("label") not in scene_edges.LABELS
                    or not _integer(evidence) or evidence not in lookup
                    or decision.get("evidence") != lookup[evidence]["text"]
                    or isinstance(score, bool) or not isinstance(score, (int, float))
                    or not math.isfinite(score) or not 0 <= score <= 1
                    or not isinstance(decision.get("reason"), str) or not decision["reason"].strip()
                    or len(decision["reason"]) > 120
                    or not isinstance(bounds, dict) or set(bounds) != {"first_id", "last_id"}
                    or not _integer(bounds["first_id"]) or not _integer(bounds["last_id"])
                    or not 0 <= bounds["first_id"] <= identifier <= bounds["last_id"] < len(units)):
                raise ProcessingError("Scene audit contains an invalid source judgment")
            if decision["label"] == "mixed" and (bounds["first_id"] != bounds["last_id"] or evidence != identifier):
                raise ProcessingError("A mixed scene unit must have its own source evidence")
        if row["intent"]["label"] != "editorial" and row["boundary"] is None:
            raise ProcessingError("A possible promotion is missing its boundary audit")
    return audit


def _cut_units(cuts, units, audit):
    covered = set()
    for cut in cuts:
        if cut["approved"] or cut.get("requires_review") is not True:
            raise ProcessingError("Classifier stages must return manual proposals")
        records = cut.get("verification")
        if not isinstance(records, list) or not records:
            raise ProcessingError("A proposed cut is missing its source verification")
        identifiers = []
        for row in records:
            identifier = row.get("segment_id") if isinstance(row, dict) else None
            if not _integer(identifier) or identifier not in audit or row != audit[identifier]:
                raise ProcessingError("Cut verification does not match the complete source audit")
            identifiers.append(identifier)
        actual = {unit["id"] for unit in units
                  if cut["start"] <= unit["start"] <= unit["end"] <= cut["end"]}
        partial = any(unit["start"] < cut["end"] and unit["end"] > cut["start"]
                      and unit["id"] not in actual for unit in units)
        if len(identifiers) != len(set(identifiers)) or set(identifiers) != actual or partial or actual & covered:
            raise ProcessingError("Cut source coverage is partial, duplicated, or missing")
        if cut["start"] != min(units[i]["start"] for i in actual) or cut["end"] != max(units[i]["end"] for i in actual):
            raise ProcessingError("Cut endpoints must match original source units")
        covered.update(actual)
    return covered


def _endpoint_audit(audit, plan, units, parents):
    targets = {identifier for scene in plan["scenes"] for identifier in scene["target_ids"]}
    if not isinstance(audit, dict) or set(audit) != targets or any(not _integer(i) for i in audit):
        raise ProcessingError("Scene processing requires the complete endpoint audit")
    for request in plan["requests"]:
        rows = []
        for identifier in request["target_ids"]:
            row = audit[identifier]
            if not isinstance(row, dict):
                raise ProcessingError("Endpoint audit contains an invalid judgment")
            rows.append({key: row.get(key) for key in ("id", "label", "role", "evidence_id", "reason")})
        try:
            parsed = scene_edges._parse({"decisions": rows}, request)
        except ValueError as error:
            raise ProcessingError("Endpoint audit failed its source contract") from error
        for identifier, decision in parsed.items():
            row = audit[identifier]
            scene_ids = [scene["scene_id"] for scene in plan["scenes"] if identifier in scene["target_ids"]]
            if (not _integer(row.get("segment_id")) or row["segment_id"] != identifier
                    or row.get("evidence") != decision["evidence"] or row.get("scene_ids") != scene_ids
                    or not isinstance(row.get("trimmed"), bool) or not isinstance(row.get("requires_review"), bool)):
                raise ProcessingError("Endpoint audit ownership or source evidence is invalid")
    aligned = {unit["id"] for unit in units if unit.get("words")
               and unit["words"][0]["start"] == unit["start"] and unit["words"][-1]["end"] == unit["end"]}
    aligned -= {identifier for _, identifiers in parents if len(identifiers) > 1 for identifier in identifiers}
    trimmed = set()
    for scene in plan["scenes"]:
        for direction in (scene["selected_unit_ids"], list(reversed(scene["selected_unit_ids"]))):
            previous = None
            for identifier in direction:
                row = audit.get(identifier)
                if (previous is not None and abs(identifier - previous) != 1
                        or row is None or row["label"] != "editorial" or identifier not in aligned):
                    break
                trimmed.add(identifier)
                previous = identifier
    for identifier, row in audit.items():
        status = "trimmed_editorial_edge" if identifier in trimmed else "unaligned_review" if identifier not in aligned else "retained"
        if (row["trimmed"] != (identifier in trimmed) or row.get("trim_status") != status
                or row["requires_review"] != (row["label"] in {"mixed", "uncertain"} or identifier not in aligned)):
            raise ProcessingError("Endpoint trim metadata does not match aligned contiguous source edges")
    return trimmed


def _refinement(proposals, refined, trimmed, units):
    expected = []
    for cut in proposals:
        spans = [(cut["start"], cut["end"])]
        for identifier in sorted(trimmed):
            left, right = units[identifier]["start"], units[identifier]["end"]
            spans = [(a, b) for start, end in spans for a, b in
                     ([(start, end)] if right <= start or left >= end else
                      ([(start, left)] if start < left else []) + ([(right, end)] if right < end else []))]
        for start, end in spans:
            identifiers = tuple(row["segment_id"] for row in cut["verification"]
                                if row["segment_id"] not in trimmed
                                and start <= units[row["segment_id"]]["start"] <= units[row["segment_id"]]["end"] <= end)
            expected.append((start, end, identifiers))
    actual = [(cut["start"], cut["end"], tuple(row["segment_id"] for row in cut["verification"])) for cut in refined]
    if sorted(actual) != sorted(expected):
        raise ProcessingError("Endpoint review changed the proposed interior or omitted an unresolved cut")


def _approval(cuts, units, parents, audit, endpoint_audit, plan, threshold, review_only):
    retained = {row["segment_id"] for cut in cuts for row in cut["verification"]}
    coarse = {identifier for _, identifiers in parents if len(identifiers) > 1 for identifier in identifiers}
    scenes, endpoint_ids = {}, set()
    for scene in plan["scenes"]:
        identifiers = [i for i in scene["selected_unit_ids"] if i in retained]
        reasons, ends = [], []
        if identifiers:
            ends = sorted({identifiers[0], identifiers[-1]})
            endpoint_ids.update(ends)
            if any(i not in endpoint_audit or endpoint_audit[i]["label"] != "commercial"
                   or endpoint_audit[i]["trimmed"] for i in ends):
                reasons.append("retained_scene_endpoint_not_commercial")
            if any(endpoint_audit[i]["label"] != "commercial"
                   for i in scene["target_ids"] if i in retained):
                reasons.append("retained_endpoint_requires_review")
        scenes[scene["scene_id"]] = {"unit_ids": identifiers, "endpoint_ids": ends, "reasons": reasons}
    proofs, resolved = [], set()
    for cut in cuts:
        identifiers = [row["segment_id"] for row in cut["verification"]]
        scene_ids = [key for key, scene in scenes.items() if set(identifiers) & set(scene["unit_ids"])]
        reasons, agreements, scores = set(), [], []
        if not scene_ids:
            reasons.add("missing_scene_ownership")
        for scene_id in scene_ids:
            reasons.update(scenes[scene_id]["reasons"])
        for identifier in identifiers:
            unit, row, edge = units[identifier], audit[identifier], endpoint_audit.get(identifier)
            first, second = row["intent"], row["boundary"]
            words = unit.get("words") or []
            if (identifier in coarse or not words or words[0]["start"] != unit["start"]
                    or words[-1]["end"] != unit["end"] or _words_text(words) != unit["text"]):
                reasons.add("unaligned_or_coarse_source")
            if unit.get("asr_recovered") or any(word.get("probability", 1) < .35 for word in words):
                reasons.add("uncertain_or_recovered_words")
            if second is None or second["label"] != "commercial" or second["confidence"] < threshold:
                reasons.add("commercial_boundary_below_threshold_or_missing")
            else:
                scores.append(second["confidence"])
            recovery = (first["label"] == "editorial" and identifier in endpoint_ids and edge is not None
                        and edge["label"] == "commercial" and not edge["trimmed"]
                        and second is not None and second["label"] == "commercial" and second["confidence"] >= threshold)
            if first["label"] == "commercial" and first["confidence"] >= threshold:
                scores.append(first["confidence"])
                if second is not None and second["label"] == "commercial" and second["confidence"] >= threshold:
                    agreements.append({"segment_id": identifier, "commercial_stages": ["intent", "boundary"]})
            elif recovery:
                agreements.append({"segment_id": identifier, "commercial_stages": ["boundary", "endpoint"],
                                   "endpoint_confidence": None, "endpoint_role": edge["role"]})
                resolved.add(identifier)
            else:
                reasons.add("intent_disagreement_or_below_threshold")
            if first["label"] in {"mixed", "uncertain"} or (second and second["label"] in {"mixed", "uncertain"}):
                reasons.add("mixed_or_uncertain_judgment")
        eligible = not reasons
        proof = {"policy_version": POLICY_VERSION, "eligible": eligible, "approved": eligible and not review_only,
                 "manual_review_setting": review_only, "threshold": threshold, "unit_ids": identifiers,
                 "scene_ids": scene_ids, "endpoint_ids": sorted({i for key in scene_ids for i in scenes[key]["endpoint_ids"]}),
                 "commercial_agreements": agreements, "minimum_scored_confidence": min(scores) if scores else None,
                 "reasons": sorted(reasons), "confidence_semantics": "Model scores are uncalibrated; endpoint roles are unscored."}
        cut.update(approved=proof["approved"], requires_review=not eligible,
                   approval_metadata=deepcopy(proof), pipeline_policy_version=POLICY_VERSION)
        if eligible:
            cut["label"] = "commercial"
            cut["confidence"] = proof["minimum_scored_confidence"]
        proofs.append(proof)
    trimmed = {i for i, row in endpoint_audit.items() if row["trimmed"]}
    unresolved = []
    for identifier, row in audit.items():
        first, second = row["intent"], row["boundary"]
        if first["label"] in {"mixed", "uncertain"} or second is not None and second["label"] in {"mixed", "uncertain"}:
            unresolved.append(identifier)
        elif second is not None and first["label"] == "commercial" and second["label"] == "editorial":
            # Confident disagreement still needs a listener decision. A negative
            # boundary judgment must not silently erase positive intent evidence.
            unresolved.append(identifier)
        elif identifier in trimmed | resolved:
            continue
        elif second is None:
            if first["confidence"] < threshold:
                unresolved.append(identifier)
        elif second["label"] == "editorial":
            # Only agreeing editorial judgments can resolve this negative branch.
            if second["confidence"] < threshold:
                unresolved.append(identifier)
        elif (first["label"] != second["label"] or first["confidence"] < threshold
              or second["confidence"] < threshold):
            unresolved.append(identifier)
    unresolved.sort()
    if not cuts and unresolved:
        raise ProcessingError("Scene judgments remain unresolved; an empty result cannot be reported as no advertisements")
    for cut, proof in zip(cuts, proofs):
        proof["global_unresolved_unit_ids"] = unresolved[:]
        proof["global_requires_review"] = bool(unresolved)
        if unresolved:
            # Preserve the local proof while withholding every automatic edit:
            # callers that store only cuts must still see an incomplete job.
            proof["approved"] = False
            proof["reasons"] = sorted(set(proof["reasons"]) | {"unresolved_source_judgments"})
            cut.update(approved=False, requires_review=True)
        cut["approval_metadata"] = deepcopy(proof)
    return proofs, unresolved


def classify_scene_pipeline(transcript, *, base_url, model, key="", threshold=.90, review_only=True,
                            progress=None, should_cancel=None, window_chars=18000, context_segments=12,
                            request_timeout=180, allow_redirects=False, trust_env=False, ai_reasoning=False,
                            decision_sink=None, edge_decision_sink=None, approval_sink=None):
    """Return refined cuts and publish complete audits only after all checks pass.

    ``decision_sink`` receives the unchanged full scene audit; ``edge_decision_sink``
    receives the complete endpoint audit. ``approval_sink`` receives both audits,
    the source plan, per-cut proof, and unresolved source IDs. Stage profiles are
    fixed regardless of the saved legacy reasoning checkbox. No hosted endpoint,
    key, redirect, or environment proxy is accepted, even for the initial sweep.
    """
    _preflight(base_url, model, key, threshold, review_only, request_timeout,
               allow_redirects, trust_env, ai_reasoning, (decision_sink, edge_decision_sink, approval_sink))
    source = validate_transcript(deepcopy(transcript))
    original = deepcopy(source)
    units, parents = _review_units(source["segments"])
    _check_cancel(should_cancel)
    received, sink = _capture("Scene classifier")
    proposals = scene_review.classify_scenes(source, base_url=base_url, model=model, key="", threshold=threshold,
        review_only=True, progress=progress, should_cancel=should_cancel, window_chars=window_chars,
        context_segments=context_segments, request_timeout=request_timeout, allow_redirects=False,
        trust_env=False, ai_reasoning=False, decision_sink=sink)
    if source != original or len(received) != 1:
        raise ProcessingError("Scene classifier changed source units or omitted its audit")
    audit = _scene_audit(received[0], units)
    proposals = validate_cuts(deepcopy(proposals), source["duration"])
    selected = _cut_units(proposals, units, audit)
    # Coarse parents may include editorial child units, but cannot lose a child
    # whose focused boundary actually proposed a commercial or unresolved scene.
    required = {i for i, row in audit.items() if row["boundary"] is not None and row["boundary"]["label"] != "editorial"}
    if not required <= selected:
        raise ProcessingError("Scene proposals omitted a noneditorial boundary judgment")
    plan = scene_edges.build_edge_plan(source, proposals, audit)
    _check_cancel(should_cancel)
    received_edges, sink_edges = _capture("Endpoint classifier")
    refined = scene_edges.refine_scene_edges(source, deepcopy(proposals), deepcopy(audit), base_url=base_url,
        model=model, key="", progress=progress, should_cancel=should_cancel, decision_sink=sink_edges,
        timeout=request_timeout, allow_redirects=False, trust_env=False)
    if source != original or len(received_edges) != 1:
        raise ProcessingError("Endpoint classifier changed source units or omitted its audit")
    edge_audit = received_edges[0]
    trimmed = _endpoint_audit(edge_audit, plan, units, parents)
    refined = validate_cuts(deepcopy(refined), source["duration"])
    _cut_units(refined, units, audit)
    _refinement(proposals, refined, trimmed, units)
    proofs, unresolved = _approval(refined, units, parents, audit, edge_audit, plan, threshold, review_only)
    result = validate_cuts(refined, source["duration"])
    summary = {"policy_version": POLICY_VERSION, "scene_policy_version": scene_review.POLICY_VERSION,
               "edge_policy_version": scene_edges.POLICY_VERSION,
               "scene_inference_profile": scene_review.INFERENCE_PROFILE,
               "edge_inference_profile": scene_edges.INFERENCE_PROFILE,
               "scene_audit": deepcopy(audit), "endpoint_audit": deepcopy(edge_audit), "edge_plan": deepcopy(plan),
               "cut_approvals": deepcopy(proofs), "unresolved_unit_ids": unresolved,
               "requires_review": bool(unresolved) or any(cut["requires_review"] for cut in result)}
    for cut in result:
        cut["approval_metadata"]["unresolved_source_unit_ids"] = unresolved[:]
    _check_cancel(should_cancel)
    if decision_sink is not None:
        decision_sink(deepcopy(audit))
    if edge_decision_sink is not None:
        edge_decision_sink(deepcopy(edge_audit))
    if approval_sink is not None:
        approval_sink(summary)
    return result
