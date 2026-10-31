"""Refine scene boundaries without reopening globally protected editorial words.

This module is pure orchestration. The caller supplies native token preflight
and inference callbacks; complete scenes are never cropped to fit a request.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math


POLICY_VERSION = "complete-scene-boundaries-v1"
GLOBAL_LABELS = ("commercial", "editorial", "mixed", "uncertain")
WORD_LABELS = ("commercial", "editorial", "uncertain")


class BoundaryReviewError(ValueError):
    """The source or model output cannot support a complete boundary decision."""


def _integer(value):
    return type(value) is int


def _time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _validate_source(units, labels):
    if (not isinstance(units, list) or not isinstance(labels, dict)
            or any(not isinstance(unit, dict) or type(unit.get("id")) is not int
                   or unit["id"] != index for index, unit in enumerate(units))
            or set(labels) != set(range(len(units)))
            or any(not _integer(key) or not isinstance(value, str) or value not in GLOBAL_LABELS
                   for key, value in labels.items())):
        raise BoundaryReviewError("Boundary review requires complete ordered source-unit decisions")
    for unit in units:
        if (not isinstance(unit.get("text"), str) or not isinstance(unit.get("words"), list)
                or not unit["words"]):
            raise BoundaryReviewError("Boundary review requires every original source word")
        for word in unit["words"]:
            if (not isinstance(word, dict) or not isinstance(word.get("word"), str)
                    or not word["word"].strip() or not _time(word.get("start"))
                    or not _time(word.get("end")) or word["end"] < word["start"]):
                raise BoundaryReviewError("Boundary review requires valid original word timestamps")


def _render_request(scene_blocks, words):
    source_ids = [identifier for block in scene_blocks for identifier in block["target_word_ids"]]
    local_ids = {identifier: index for index, identifier in enumerate(source_ids)}
    if len(local_ids) != len(source_ids):
        raise BoundaryReviewError("Boundary scenes cannot own the same target word twice")
    targets = [dict(deepcopy(words[identifier]), id=index, source_word_id=identifier)
               for index, identifier in enumerate(source_ids)]
    passages = []
    for block in scene_blocks:
        lines = [f'SCENE {block["scene_id"]} (complete scene with protected neighboring context)']
        target_set = set(block["target_word_ids"])
        for row in block["context_units"]:
            if target_set.intersection(row["word_ids"]):
                text = " ".join(f'[{local_ids[i]}] {words[i]["text"]}' for i in row["word_ids"])
                lines.append(f'SOURCE UNIT {row["id"]}, TARGET WORDS: {text}')
            else:
                role = "PROTECTED CONTEXT" if row["label"] == "editorial" else "SCENE CONTEXT"
                lines.append(f'SOURCE UNIT {row["id"]}, {role}: {row["text"]}')
        passages.append("\n".join(lines))
    return {"scene_ids": [block["scene_id"] for block in scene_blocks],
            "units": targets, "source_word_ids": source_ids,
            "scene_context": "\n\n".join(passages)}


def build_boundary_plan(units, labels):
    """Target scene edges and every mixed/uncertain unit, never editorial units.

Source word IDs are immutable across the episode. Request IDs are dense and
local, including when complete scenes are packed into different requests.
"""
    _validate_source(units, labels)
    words, parent_words, defaults = [], {}, {}
    for unit in units:
        parent_words[unit["id"]] = []
        for word in unit["words"]:
            identifier = len(words)
            words.append({"id": identifier, "parent_id": unit["id"],
                          "text": word["word"].strip(), "word": deepcopy(word),
                          "start": word["start"], "end": word["end"],
                          "asr_recovered": bool(unit.get("asr_recovered"))})
            parent_words[unit["id"]].append(identifier)
            label = labels[unit["id"]]
            defaults[identifier] = label if label in WORD_LABELS else "uncertain"

    runs = []
    for identifier in range(len(units)):
        if labels[identifier] == "editorial":
            continue
        if not runs or identifier != runs[-1][-1] + 1:
            runs.append([])
        runs[-1].append(identifier)

    scenes = []
    for scene_id, run in enumerate(runs):
        selected = set(run[:2] + run[-2:])
        selected.update(i for i in run if labels[i] in {"mixed", "uncertain"})
        left, right = run[0], run[-1] + 1
        for _ in range(2):
            if left > 0 and labels[left - 1] == "editorial":
                left -= 1
            if right < len(units) and labels[right] == "editorial":
                right += 1
        targets = [i for parent in sorted(selected) for i in parent_words[parent]]
        scenes.append({"scene_id": scene_id, "scene_unit_ids": list(run),
                       "context_unit_ids": list(range(left, right)),
                       "target_unit_ids": sorted(selected), "target_word_ids": targets,
                       "context_units": [{"id": i, "label": labels[i], "text": units[i]["text"],
                                          "word_ids": list(parent_words[i])}
                                         for i in range(left, right)]})
    return {"policy_version": POLICY_VERSION, "words": words, "defaults": defaults,
            "global_decisions": dict(labels), "scene_blocks": scenes,
            "target_word_ids": [i for block in scenes for i in block["target_word_ids"]],
            "requests": [_render_request(scenes, words)] if scenes else []}


def pack_boundary_requests(plan, fits):
    """Use native token preflight to pack whole scenes, rejecting oversized ones.

All batches are preflighted before inference begins. ``fits`` must include the
caller's real chat template, system prompt, schema and output/reasoning reserve.
"""
    if not callable(fits):
        raise TypeError("Boundary review requires a native token preflight callback")
    scenes, words = plan["scene_blocks"], plan["words"]
    if not scenes:
        return []

    def checked_fit(block):
        result = fits(deepcopy(block))
        if type(result) is not bool:
            raise TypeError("Boundary token preflight must return a boolean")
        return result

    complete = _render_request(scenes, words)
    if checked_fit(complete):
        return [complete]
    batches, pending = [], []
    for scene in scenes:
        combined = _render_request(pending + [scene], words)
        if checked_fit(combined):
            pending.append(scene)
            continue
        if not pending:
            raise BoundaryReviewError(f'Complete scene {scene["scene_id"]} exceeds the native context budget')
        batches.append(_render_request(pending, words))
        single = _render_request([scene], words)
        if not checked_fit(single):
            raise BoundaryReviewError(f'Complete scene {scene["scene_id"]} exceeds the native context budget')
        pending = [scene]
    if pending:
        batches.append(_render_request(pending, words))
    return batches


def parse_word_partition(answer, identifiers):
    """Require exactly one decision per dense target ID; mixed is not a word role."""
    if (not isinstance(identifiers, list) or not identifiers
            or any(not _integer(i) for i in identifiers)
            or identifiers != list(range(len(identifiers)))):
        raise BoundaryReviewError("Boundary target IDs must be a nonempty dense local block")
    if isinstance(answer, dict) and set(answer) == {"ranges"}:
        if not isinstance(answer["ranges"], list) or not answer["ranges"]:
            raise BoundaryReviewError("Boundary output must be a complete word partition")
        decisions, first = {}, 0
        for row in answer["ranges"]:
            if (not isinstance(row, dict) or set(row) != {"end_id", "label"}
                    or not _integer(row["end_id"]) or not first <= row["end_id"] < len(identifiers)
                    or not isinstance(row["label"], str) or row["label"] not in WORD_LABELS):
                raise BoundaryReviewError("Boundary output invented, reversed or mixed a word decision")
            decisions.update((i, row["label"]) for i in range(first, row["end_id"] + 1))
            first = row["end_id"] + 1
        if first != len(identifiers):
            raise BoundaryReviewError("Boundary output omitted target words")
        return decisions
    if (not isinstance(answer, dict) or set(answer) != set(identifiers)
            or any(not _integer(i) or not isinstance(label, str) or label not in WORD_LABELS
                   for i, label in answer.items())):
        raise BoundaryReviewError("Boundary callback omitted or invented target-word decisions")
    return dict(answer)


def refine_boundaries(plan, *, request, fits):
    """Return full source-word decisions and replayable boundary ownership audit."""
    if not callable(request):
        raise TypeError("Boundary review requires an inference callback")
    requests = pack_boundary_requests(plan, fits)
    decisions, votes, audit_requests = dict(plan["defaults"]), {}, []
    for block in requests:
        answer = request(deepcopy(block))
        local = parse_word_partition(answer, list(range(len(block["units"]))))
        for identifier, label in local.items():
            source_id = block["source_word_ids"][identifier]
            if source_id in votes:
                raise BoundaryReviewError("Boundary review classified a target word twice")
            decisions[source_id] = label
            votes[source_id] = [label]
        digest = hashlib.sha256(json.dumps(block, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False).encode()).hexdigest()
        audit_requests.append({"scene_ids": list(block["scene_ids"]),
                               "source_word_ids": list(block["source_word_ids"]),
                               "request_sha256": digest})
    if set(votes) != set(plan["target_word_ids"]):
        raise BoundaryReviewError("Boundary review did not classify every target word")
    audit = {"policy_version": POLICY_VERSION, "source_word_count": len(plan["words"]),
             "target_word_ids": list(plan["target_word_ids"]),
             "scene_blocks": [{key: deepcopy(value) for key, value in block.items()
                               if key != "context_units"} for block in plan["scene_blocks"]],
             "requests": audit_requests, "word_votes": votes,
             "unresolved_word_ids": [i for i, label in decisions.items() if label == "uncertain"]}
    return decisions, audit
