"""Deterministic omission warnings, never commercial classifications.

An editorial decision containing direct sponsorship, transactional offer or other-podcast promotion cues
needs review. Even fiction can contain those cues, so this guard cannot justify
a cut, change a model label, or establish that a recording is free of ads.
"""

from __future__ import annotations

import math
import re


VERSION = "sponsor-promo-cue-omissions-v2"
LABELS = {"editorial", "commercial", "mixed", "uncertain"}
CLUSTER_SECONDS = 45
_SPONSOR = re.compile(
    r"\b(?:this|today'?s|the|our)\s+(?:episode|show|podcast)\s+(?:is\s+)?"
    r"(?:brought\s+to\s+you|sponsored|supported)\s+by\b", re.I)
_CODE = re.compile(r"\b(?:promo(?:tional)?|coupon|discount)\s+code\b", re.I)
_PODCAST_PROMO = re.compile(
    r"\b(?:listen\s+to|follow|check\s+out|download|subscribe\s+to|discover)\s+"
    r"(?:(?:our|my|the)\s+)?(?:other|another|sister|spin-?off)\s+(?:free\s+)?podcast\b", re.I)
_ACTION = re.compile(
    r"\b(?:sign\s+up|shop|buy|order|visit|head\s+to|go\s+to|download|get\s+started|join|"
    r"(?:use|enter|apply)\s+(?:(?:the|our|my)\s+)?code)\b", re.I)
_OFFER = re.compile(
    r"\b(?:free\s+trial|trial\s+(?:period|offer)|paid\s+subscription|"
    r"terms(?:\s+and\s+conditions)?\s+apply|new\s+(?:users|customers)|"
    r"first\s+month|free\s+shipping|per\s+month|a\s+month|discount|"
    r"percent\s+off|\d+\s*%\s*off|(?:save|get)\s+\d+\s*(?:dollars?|percent))\b",
    re.I)


def omission_guard(units, global_decisions):
    """Audit cues in contiguous editorial context without changing any labels.

Only one adjacent sentence on either side supplies context. Matches are tied to
the actual source units containing the cue; an adjacent commercial unit cannot
cause a correctly protected program announcement to be flagged.
"""
    if (not isinstance(units, list) or not isinstance(global_decisions, dict)
            or set(global_decisions) != set(range(len(units)))
            or any(type(i) is not int or not isinstance(label, str) or label not in LABELS
                   for i, label in global_decisions.items())):
        raise ValueError("Omission guard requires complete ordered global decisions")
    for i, unit in enumerate(units):
        if (not isinstance(unit, dict) or type(unit.get("id")) is not int or unit["id"] != i
                or not isinstance(unit.get("text"), str)
                or any(type(unit.get(key)) not in (int, float) or not math.isfinite(unit[key])
                       or unit[key] < 0 for key in ("start", "end"))
                or unit["end"] < unit["start"]):
            raise ValueError("Omission guard requires valid original source units")

    hits = {}

    def remember(identifiers, family):
        for identifier in identifiers:
            hits.setdefault(identifier, set()).add(family)

    for i, unit in enumerate(units):
        if global_decisions[i] != "editorial":
            continue
        if _SPONSOR.search(unit["text"]):
            remember([i], "explicit_sponsorship")
        if _CODE.search(unit["text"]):
            remember([i], "offer_code")
        if _PODCAST_PROMO.search(unit["text"]):
            remember([i], "explicit_podcast_promotion")
        first = i - 1 if i > 0 and global_decisions[i - 1] == "editorial" else i
        last = i + 1 if i + 1 < len(units) and global_decisions[i + 1] == "editorial" else i
        text, spans = "", []
        for identifier in range(first, last + 1):
            if text:
                text += " "
            start = len(text)
            text += units[identifier]["text"]
            spans.append((identifier, start, len(text)))

        def owners(match):
            return {identifier for identifier, start, end in spans
                    if match.start() < end and match.end() > start}

        actions, offers = list(_ACTION.finditer(text)), list(_OFFER.finditer(text))
        for action in actions:
            for offer in offers:
                identifiers = owners(action) | owners(offer)
                # Distant source timestamps must not be joined merely because
                # an incomplete transcript placed two units next to each other.
                if (identifiers and units[max(identifiers)]["end"] - units[min(identifiers)]["start"] <= 90):
                    remember(identifiers, "action_with_offer")

    clusters = []
    for identifier in sorted(hits):
        previous = clusters[-1][-1] if clusters else None
        if (previous is None or units[identifier]["start"] - units[previous]["end"] > CLUSTER_SECONDS
                or any(global_decisions[i] != "editorial" for i in range(previous + 1, identifier))):
            clusters.append([])
        clusters[-1].append(identifier)
    return {"version": VERSION, "flagged_unit_ids": sorted(hits),
            "cue_clusters": [{"unit_ids": list(range(group[0], group[-1] + 1)),
                              "matched_unit_ids": list(group),
                              "cue_families": sorted({family for i in group for family in hits[i]}),
                              "start": units[group[0]]["start"], "end": units[group[-1]]["end"]}
                             for group in clusters],
            "guard_requires_review": bool(hits)}
