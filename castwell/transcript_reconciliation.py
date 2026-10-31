"""Conservative local speech repair between uniquely matched word anchors."""
from __future__ import annotations

from copy import deepcopy
import math
import re


VERSION = "anchored-gap-reconciliation-v1"
ANCHOR_WORDS = 3
MAX_CONTEXT_SECONDS = 4.0
MAX_ANCHOR_SHIFT_SECONDS = 2.0


def _token(word):
    return "".join(re.findall(r"\w+", word["word"].casefold()))


def _words(transcript):
    return [word for segment in transcript["segments"] for word in segment.get("words", [])]


def plan_reconciliation(transcript, start, end):
    """Choose two complete nearby anchors without crossing another long gap."""
    words = _words(transcript)
    left = [index for index, word in enumerate(words) if word["end"] <= start]
    right = [index for index, word in enumerate(words) if word["start"] >= end]
    if len(left) < ANCHOR_WORDS or len(right) < ANCHOR_WORDS:
        return None
    left, right = left[-ANCHOR_WORDS:], right[:ANCHOR_WORDS]
    for group in (left, right):
        selected = [words[index] for index in group]
        if (any(not _token(word) or word["end"] <= word["start"] for word in selected)
                or any(second["start"] - first["end"] >= 3 for first, second in zip(selected, selected[1:]))):
            return None
    crop_start = max(0., min(start - 2., words[left[0]]["start"] - .2))
    crop_end = min(transcript["duration"], max(end + 2., words[right[-1]]["end"] + .2))
    if start - crop_start > MAX_CONTEXT_SECONDS or crop_end - end > MAX_CONTEXT_SECONDS:
        return None
    # Wordless source text cannot be proved retained by matching word anchors.
    if any(not segment.get("words") and segment["start"] < words[right[-1]]["end"]
           and segment["end"] > words[left[0]]["start"] for segment in transcript["segments"]):
        return None
    return {"start_word": left[0], "end_word": right[-1] + 1, "left_anchor": deepcopy([words[i] for i in left]),
            "right_anchor": deepcopy([words[i] for i in right]), "crop_start": crop_start, "crop_end": crop_end,
            "gap_start": start, "gap_end": end}


def _matches(values, target):
    return [index for index in range(len(values) - len(target) + 1) if values[index:index + len(target)] == target]


def reconcile_words(transcript, plan, segments):
    """Accept a repair only if all old words survive, including their exact text."""
    raw, valid = [], []
    for segment in segments:
        metrics = [getattr(segment, key, None) for key in ("no_speech_prob", "avg_logprob", "compression_ratio")]
        metrics_ok = (all(isinstance(value, (int, float)) and math.isfinite(value) for value in metrics)
                      and metrics[0] <= .6 and metrics[1] >= -1 and metrics[2] <= 2.4)
        for word in getattr(segment, "words", None) or []:
            left, right, probability = (getattr(word, key, None) for key in ("start", "end", "probability"))
            if (not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (left, right, probability))
                    or not isinstance(word.word, str) or not word.word.strip()):
                return None, "invalid_serial_word"
            item = {"word": word.word, "start": left + plan["crop_start"],
                    "end": right + plan["crop_start"], "probability": probability}
            raw.append(item)
            valid.append(bool(metrics_ok and .5 <= probability <= 1 and 0 <= left < right
                              and item["end"] <= plan["crop_end"] + .05))
    tokens = [_token(word) for word in raw]
    left_hits = _matches(tokens, [_token(word) for word in plan["left_anchor"]])
    right_hits = _matches(tokens, [_token(word) for word in plan["right_anchor"]])
    if len(left_hits) != 1 or len(right_hits) != 1 or left_hits[0] + ANCHOR_WORDS > right_hits[0]:
        return None, "missing_ambiguous_or_unordered_anchors"
    left, right = left_hits[0], right_hits[0] + ANCHOR_WORDS
    proposed = deepcopy(raw[left:right])
    if (not all(valid[left:right]) or any(b["start"] < a["end"] for a, b in zip(proposed, proposed[1:]))):
        return None, "uncertain_or_overlapping_serial_words"
    for original, aligned in zip([*plan["left_anchor"], *plan["right_anchor"]],
                                 [*proposed[:ANCHOR_WORDS], *proposed[-ANCHOR_WORDS:]]):
        if any(abs(original[side] - aligned[side]) > MAX_ANCHOR_SHIFT_SECONDS for side in ("start", "end")):
            return None, "anchor_moved_too_far"
    all_words = _words(transcript)
    original = all_words[plan["start_word"]:plan["end_word"]]
    matching, cursor = [], ANCHOR_WORDS
    indices = list(range(ANCHOR_WORDS))
    for old in original[ANCHOR_WORDS:-ANCHOR_WORDS]:
        while cursor < len(proposed) - ANCHOR_WORDS and _token(proposed[cursor]) != _token(old):
            cursor += 1
        if cursor == len(proposed) - ANCHOR_WORDS:
            return None, "original_words_not_preserved"
        indices.append(cursor)
        cursor += 1
    indices.extend(range(len(proposed) - ANCHOR_WORDS, len(proposed)))
    for index, (old, cursor) in enumerate(zip(original, indices)):
        # Discovery ignores punctuation/case. Publication preserves the exact
        # old text, and retains a map to every word's previous timing evidence.
        proposed[cursor]["word"] = old["word"]
        matching.append({"original_word_index": plan["start_word"] + index,
                         "replacement_word_index": cursor, "original": deepcopy(old),
                         "start": proposed[cursor]["start"], "end": proposed[cursor]["end"]})
    before = all_words[plan["start_word"] - 1] if plan["start_word"] else None
    after = all_words[plan["end_word"]] if plan["end_word"] < len(all_words) else None
    if (proposed[0]["start"] < plan["crop_start"] or proposed[-1]["end"] > plan["crop_end"]
            or before and proposed[0]["start"] < before["end"]
            or after and proposed[-1]["end"] > after["start"]):
        return None, "outside_untouched_neighbor_bounds"
    return {"version": VERSION, "plan": deepcopy(plan), "words": proposed, "original_words": deepcopy(original),
            "original_word_mapping": matching, "added_words": len(proposed) - len(original),
            "human_listening_verified": False}, "reconciled"


def apply_reconciliations(transcript, replacements):
    """Splice nonoverlapping anchored blocks while retaining untouched content."""
    from .processing import _words_text, validate_transcript

    replacements = sorted(replacements, key=lambda item: item["plan"]["start_word"])
    for previous, current in zip(replacements, replacements[1:]):
        if previous["plan"]["end_word"] > current["plan"]["start_word"]:
            raise ValueError("Reconciliation blocks overlap")
    lookup = {replacement["plan"]["start_word"]: replacement for replacement in replacements}
    covered = {index for replacement in replacements
               for index in range(replacement["plan"]["start_word"], replacement["plan"]["end_word"])}
    segments, offset = [], 0
    for segment in transcript["segments"]:
        words = segment.get("words") or []
        if not words:
            segments.append(deepcopy(segment))
            continue
        group = []
        def flush():
            if not group:
                return
            if len(group) == len(words):
                segments.append(deepcopy(segment))
            else:
                segments.append(dict(segment, start=group[0]["start"], end=group[-1]["end"],
                                     text=_words_text(group), words=deepcopy(group),
                                     asr_pre_reconciliation_segment={key: segment[key] for key in ("id", "start", "end", "text")}))
            group.clear()
        for index, word in enumerate(words):
            identifier = offset + index
            if identifier in covered:
                flush()
                if identifier in lookup:
                    replacement = lookup[identifier]
                    new_words = deepcopy(replacement["words"])
                    segments.append({"start": new_words[0]["start"], "end": new_words[-1]["end"],
                                     "text": _words_text(new_words), "words": new_words, "asr_recovered": True,
                                     "asr_reconciled": True, "requires_review": True,
                                     "asr_reconciliation": deepcopy(replacement)})
            else:
                group.append(word)
        flush()
        offset += len(words)
    return validate_transcript(dict(transcript, segments=[dict(s, id=i) for i, s in enumerate(segments)]))
