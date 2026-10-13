"""Experimental compact scene sweep followed by focused boundary review.

The first sweep covers every unit without keyword filtering. Only potentially
commercial scenes and nearby units receive the second judgment. Untested
editorial units therefore have no boundary judgment, and this candidate never
approves a cut. Source text and timestamps always come from the input.
"""

from __future__ import annotations

import json
import math
import re
from urllib.parse import urlsplit

from .ad_review import LABELS, _BOUNDARY, _INTENT, _review_units
from .processing import ProcessingError

POLICY_VERSION = "scene-boundary-v7"
INFERENCE_PROFILE = "scene-compact-intent-boundary-reasoning256-2048"
MAX_TARGETS = 64
MAX_CORE_CHARS = 9000
MAX_CONTEXT_CHARS = 4500
MAX_CONTEXT_NEIGHBORS = 12
BOUNDARY_NEIGHBORS = 6
MAX_OUTPUT_TOKENS = 2048
BOUNDARY_REASONING_TOKENS = 256


class _InvalidRanges(ProcessingError):
    """A completed response failed the complete range partition contract."""


_RANGE_FORMAT = """
Use compact contiguous ranges instead of repeating decisions for each line.
Return an object with only ranges. Each range has first_id, last_id, reason,
evidence_id, label, and confidence. A range labels EVERY target from first_id
through last_id with the same role. Ranges must partition the supplied target_ids
exactly once in their supplied order, without gaps, overlaps, or invented IDs.
Do not label context-only IDs. Split a range whenever its role changes.
Choose evidence_id from context. Never return copied evidence text or timestamps.
Give one brief context-specific reason per range, at most 120 characters.
"""

_SCENE_BOUNDARY = """
For this focused audit, classify membership in the ENTIRE established promotional
scene, not whether an isolated sentence sounds conversational. A contiguous
problem statement, rhetorical setup, personal anecdote, or two actors joking in
dialogue is commercial when it continues directly into an actual offer that
completes its persuasive point. Speaker changes, jokes, banter, and a missing
brand name in an individual line do not end that actual commercial scene.
Check the following offer and preceding framing before excluding such a setup.
An actual return to unrelated editorial discussion does end the scene.
Pure program navigation announcing a break or return to this show, and this
show's own production credits or free contact information, are editorial.
Free previews and listening or follow invitations for another podcast are also
editorial, even inserted between ads. Their adjacent sponsor or paid-subscription
offers stay commercial; split units by role rather than extending a commercial range.
Do not absorb those separately identified editorial units into an adjacent read.
A fictional or unpaid sales sketch remains editorial even when it mimics an
offer. Unresolved commercial intent remains uncertain; do not invent payment.
Split adjacent commercial and editorial units into separate ranges. Their
presence in one larger scene does not make each unit mixed. Mixed means that
THIS individual unit itself contains both meaningful editorial and commercial
speech. A mixed range must contain exactly one target, and its evidence_id must
be that same target ID; an adjacent advertisement cannot establish a mixed unit.
"""


def inference_settings(ai_reasoning=False):
    """The whole-transcript intent sweep always disables hidden thinking."""
    if not isinstance(ai_reasoning, bool):
        raise ValueError("AI reasoning must be a boolean")
    return {"temperature": 0.7, "top_p": 0.8, "seed": 42, "max_tokens": MAX_OUTPUT_TOKENS,
            "chat_template_kwargs": {"enable_thinking": False}}


def boundary_inference_settings():
    """Only focused boundaries receive a small, explicitly bounded thinking budget."""
    return {"temperature": 1.0, "top_p": .95, "top_k": 20, "min_p": 0,
            "presence_penalty": 1.5, "repeat_penalty": 1.0, "seed": 42,
            "max_tokens": MAX_OUTPUT_TOKENS, "chat_template_kwargs": {"enable_thinking": True},
            "reasoning_budget_tokens": BOUNDARY_REASONING_TOKENS, "reasoning_format": "deepseek"}


def _bounds(window_chars, context_segments):
    if isinstance(window_chars, bool) or not isinstance(window_chars, int) or window_chars < 1:
        raise ValueError("Scene window characters must be a positive integer")
    if (isinstance(context_segments, bool) or not isinstance(context_segments, int)
            or context_segments < 0):
        raise ValueError("Scene context neighbors must be a nonnegative integer")
    return min(window_chars, MAX_CORE_CHARS), min(context_segments, MAX_CONTEXT_NEIGHBORS)


def _windows(segments, *, window_chars, context_segments, blocks=None):
    limit, neighbors = _bounds(window_chars, context_segments)
    context_limit = min(MAX_CONTEXT_CHARS, limit // 2)
    if blocks is None:
        blocks = [(0, len(segments))]
    for block_first, block_last in blocks:
        first = block_first
        while first < block_last:
            last, size = first, 0
            while last < block_last and last - first < MAX_TARGETS:
                length = len(segments[last]["text"])
                if length > limit:
                    raise ValueError("A review unit exceeds the scene window; supply shorter segments or word timestamps")
                if last > first and size + length > limit:
                    break
                last, size = last + 1, size + length
            left, right = first, last
            for direction in (-1, 1):
                used = 0
                for _ in range(neighbors):
                    index = left - 1 if direction < 0 else right
                    if (not 0 <= index < len(segments)
                            or used + len(segments[index]["text"]) > context_limit):
                        break
                    used += len(segments[index]["text"])
                    if direction < 0:
                        left -= 1
                    else:
                        right += 1
            yield segments[first:last], segments[left:right]
            first = last


def _schema(core, context):
    return {"type": "object", "additionalProperties": False, "required": ["ranges"], "properties": {
        "ranges": {"type": "array", "minItems": 1, "maxItems": len(core), "items": {
            "type": "object", "additionalProperties": False,
            "required": ["first_id", "last_id", "reason", "evidence_id", "label", "confidence"],
            "properties": {
                "first_id": {"type": "integer", "enum": [s["id"] for s in core]},
                "last_id": {"type": "integer", "enum": [s["id"] for s in core]},
                "reason": {"type": "string", "minLength": 1, "maxLength": 120},
                "evidence_id": {"type": "integer", "enum": [s["id"] for s in context]},
                "label": {"type": "string", "enum": list(LABELS)},
                "confidence": {"type": "number", "enum": [0, 0.5, 0.8, 0.9, 0.95, 0.99, 1]},
            },
        }},
    }}


def _parse(answer, core, context):
    if (not isinstance(answer, dict) or set(answer) != {"ranges"}
            or not isinstance(answer["ranges"], list) or not answer["ranges"]):
        raise ValueError("Expected complete typed ranges")
    identifiers = [unit["id"] for unit in core]
    if (not identifiers or any(isinstance(value, bool) or not isinstance(value, int) for value in identifiers)
            or any(right != left + 1 for left, right in zip(identifiers, identifiers[1:]))):
        raise ValueError("Targets must be a contiguous ordered ID block")
    targets = set(identifiers)
    evidence_sources = {unit["id"]: unit["text"] for unit in context}
    expected = identifiers[0]
    decisions = {}
    for row in answer["ranges"]:
        if (not isinstance(row, dict)
                or set(row) != {"first_id", "last_id", "reason", "evidence_id", "label", "confidence"}):
            raise ValueError("Unexpected range fields")
        first, last, evidence_id = row["first_id"], row["last_id"], row["evidence_id"]
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (first, last, evidence_id)):
            raise ValueError("Range IDs must be integers")
        if first != expected or first not in targets or last not in targets or last < first:
            raise ValueError("Missing, duplicate, overlapping, reversed, or invented range")
        if evidence_id not in evidence_sources:
            raise ValueError("Invented evidence ID")
        if not isinstance(row["label"], str) or row["label"] not in LABELS:
            raise ValueError("Invalid role")
        if row["label"] == "mixed" and (first != last or evidence_id != first):
            raise ValueError("Mixed must identify one indivisible target with its own evidence")
        if not isinstance(row["reason"], str) or not row["reason"].strip() or len(row["reason"]) > 120:
            raise ValueError("Missing or oversized reason")
        confidence = row["confidence"]
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError("Invalid confidence")
        for identifier in range(first, last + 1):
            if identifier not in targets:
                raise ValueError("A range crosses nonadjacent target IDs")
            decisions[identifier] = {
                "id": identifier, "reason": row["reason"], "label": row["label"],
                "confidence": confidence, "evidence_id": evidence_id,
                "evidence": evidence_sources[evidence_id],
                "source_range": {"first_id": first, "last_id": last},
            }
        expected = last + 1
    if expected != identifiers[-1] + 1 or set(decisions) != targets:
        raise ValueError("The model omitted target units")
    return decisions


def classify_range_pass(core, context, *, stage="intent", base_url, model, key="", timeout=180,
                        allow_redirects=True, trust_env=True, should_cancel=None, ai_reasoning=False):
    """Perform one independently callable, fully validated range request."""
    import requests
    from .processing import _check_cancel, _classifier_request

    if stage not in {"intent", "boundaries"}:
        raise ValueError("Unknown scene review stage")
    _check_cancel(should_cancel)
    prompt = (_INTENT if stage == "intent" else _BOUNDARY).replace(
        "Return every target exactly once.", "Cover every target exactly once using contiguous ranges.").replace(
        "Reason must explain the context-specific distinction in at most 300 characters",
        "Reason must explain the context-specific distinction in at most 120 characters")
    if stage == "boundaries":
        prompt += _SCENE_BOUNDARY
    prompt += _RANGE_FORMAT
    settings = inference_settings(ai_reasoning)
    if stage == "boundaries":
        settings = boundary_inference_settings()
    payload = {
        "model": model, **settings,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "podcast_scenes", "strict": True, "schema": _schema(core, context)}},
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"target_ids": [unit["id"] for unit in core],
                "context": [{"id": unit["id"], "text": unit["text"]} for unit in context]}, ensure_ascii=False)},
        ],
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    response = None
    try:
        response = _classifier_request(base_url.rstrip("/") + "/chat/completions", headers, payload,
                                       should_cancel, timeout, allow_redirects=allow_redirects, trust_env=trust_env)
        _check_cancel(should_cancel)
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") not in (None, "stop"):
            raise ValueError("Incomplete model output")
        content = choice["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Missing JSON text")
        content = re.sub(r"^\s*<think>.*?</think>\s*", "", content, flags=re.S)
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        return _parse(json.loads(content), core, context)
    except requests.RequestException:
        raise ProcessingError("Scene classification request failed. No new cuts were saved.") from None
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise _InvalidRanges("Scene classification returned incomplete ranges or unsupported evidence; no new cuts were saved.") from None
    finally:
        if response is not None and callable(getattr(response, "close", None)):
            response.close()


def _run_stage(windows, *, stage, progress, should_cancel, **options):
    from .processing import _check_cancel

    pending = [(core, context, 0) for core, context in windows]
    decisions = {}
    index = 0
    while pending:
        core, context, depth = pending.pop(0)
        index += 1
        _check_cancel(should_cancel)
        if progress:
            progress(f"Checking scene {stage}: window {index} ({len(pending)} remaining)")
        try:
            result = classify_range_pass(core, context, stage=stage, should_cancel=should_cancel, **options)
        except _InvalidRanges:
            if depth >= 5 or len(core) < 2:
                raise
            if progress:
                progress("Retrying smaller scene windows after incomplete ranges")
            middle = len(core) // 2
            pending[:0] = [(core[:middle], context, depth + 1), (core[middle:], context, depth + 1)]
            continue
        if set(decisions) & set(result):
            raise ProcessingError("Scene target ownership overlapped; no new cuts were saved.")
        decisions.update(result)
    return decisions


def _focused_blocks(segments, intent, neighbors):
    selected = set()
    for index, unit in enumerate(segments):
        if intent[unit["id"]]["label"] != "editorial":
            selected.update(range(max(0, index - neighbors), min(len(segments), index + neighbors + 1)))
    blocks = []
    for index in sorted(selected):
        if blocks and blocks[-1][1] == index:
            blocks[-1] = (blocks[-1][0], index + 1)
        else:
            blocks.append((index, index + 1))
    return blocks


def classify_scenes(transcript, *, base_url, model, key="", threshold=.90, review_only=True,
                    progress=None, should_cancel=None, window_chars=18000, context_segments=12,
                    request_timeout=180, allow_redirects=True, trust_env=True, ai_reasoning=False,
                    decision_sink=None):
    """Return unapproved cuts; optionally deliver the complete audit to a callback.

    ``decision_sink(audit)`` runs once after successful validation. Each unit's
    audit has an intent judgment and either a boundary judgment or ``None``.
    The legacy reasoning argument is accepted for API compatibility; this
    candidate fixes nonthinking intent and a 256-token focused boundary budget.
    """
    from .processing import _check_cancel, validate_cuts

    inference_settings(ai_reasoning)
    _, neighbors = _bounds(window_chars, context_segments)
    if decision_sink is not None and not callable(decision_sink):
        raise ValueError("Decision sink must be a callback")
    parsed = urlsplit(base_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(character.isspace() for character in base_url) or "\\" in base_url):
        raise ValueError("Classifier URL must be HTTP(S) without credentials, query, or fragment")
    segments, parents = _review_units(transcript["segments"])
    # Validate the full target partition before making even the first request.
    windows = list(_windows(segments, window_chars=window_chars, context_segments=context_segments))
    options = {"base_url": base_url, "model": model, "key": key, "timeout": request_timeout,
               "allow_redirects": allow_redirects, "trust_env": trust_env, "ai_reasoning": False}
    intent = _run_stage(windows, stage="intent", progress=progress, should_cancel=should_cancel, **options)
    blocks = _focused_blocks(segments, intent, min(BOUNDARY_NEIGHBORS, neighbors))
    boundary_windows = list(_windows(segments, window_chars=window_chars,
                                    context_segments=context_segments, blocks=blocks))
    boundaries = _run_stage(boundary_windows, stage="boundaries", progress=progress,
                            should_cancel=should_cancel, **options)
    decisions, audit = {}, {}
    for segment in segments:
        identifier = segment["id"]
        first, second = intent[identifier], boundaries.get(identifier)
        audit[identifier] = {"segment_id": identifier, "parent_segment_id": segment["parent_segment_id"],
                             "intent": first, "boundary": second,
                             "boundary_audited": second is not None}
        # A positive intent sweep nominates a scene, but does not overrule an
        # explicit editorial judgment in the focused edit-boundary audit.
        # Keep both judgments in the audit even when the boundary pass vetoes
        # the proposal. A commercial boundary judgment can still recover a
        # lead-in that the first sweep labeled editorial.
        if second is None or second["label"] == "editorial":
            decisions[identifier] = None
            continue
        labels = {first["label"], second["label"]}
        low_alignment = bool(segment.get("asr_recovered")) or any(
            word.get("probability", 1) < .35 for word in segment.get("words", []))
        confidence = min(first["confidence"], second["confidence"])
        label = "commercial" if labels == {"commercial"} else "mixed" if "mixed" in labels else "uncertain"
        if "mixed" in labels:
            reason = "Commercial and editorial speech share this unit; listen and adjust its boundaries."
        elif len(labels) > 1:
            reason = "Intent and boundary checks disagree; review this possible promotion."
        elif "uncertain" in labels:
            reason = "Commercial intent is unresolved by the transcript; review before removing."
        elif low_alignment:
            reason = "Speech contains uncertain or recovered words; listen around both boundaries."
        else:
            reason = first["reason"]
        decisions[identifier] = {"confidence": confidence, "reason": reason, "requires_review": True,
                                 "approved": False, "label": label,
                                 "verification": audit[identifier]}
    cuts = []
    previous = -2
    for position, (segment, identifiers) in enumerate(parents):
        children = [decisions[identifier] for identifier in identifiers]
        proposed = [row for row in children if row is not None]
        if not proposed:
            continue
        decision = dict(proposed[0])
        decision["confidence"] = min(row["confidence"] for row in proposed)
        decision["verification"] = [audit[identifier] for identifier in identifiers]
        if len(proposed) != len(children) or len({row["label"] for row in proposed}) > 1:
            decision.update(label="mixed", reason="Commercial and editorial or uncertain sentences share a segment without word timestamps; listen and adjust its boundaries.")
        if (cuts and position == previous + 1 and segment["start"] == cuts[-1]["end"]
                and cuts[-1]["label"] == decision["label"]):
            cuts[-1]["end"] = segment["end"]
            cuts[-1]["confidence"] = min(cuts[-1]["confidence"], decision["confidence"])
            cuts[-1]["verification"].extend(decision["verification"])
        else:
            cuts.append({"start": segment["start"], "end": segment["end"], "source": "ai",
                         "sources": ["ai", "scene-intent-verifier", "focused-boundary-verifier"],
                         "policy_version": POLICY_VERSION, "inference_profile": INFERENCE_PROFILE,
                         **{key: decision[key] for key in ("confidence", "reason", "requires_review", "approved", "label")},
                         "verification": decision["verification"]})
        previous = position
    _check_cancel(should_cancel)
    result = validate_cuts(cuts, transcript["duration"])
    if decision_sink is not None:
        decision_sink(audit)
    return result
