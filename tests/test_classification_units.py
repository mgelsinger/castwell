"""Sentence assembly must preserve speech alignment without bridging gaps."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from castwell.processing import _classification_transcript, _words_text, detect_ads, validate_transcript


def segment(identifier, tokens, start=0., step=.1, recovered=False):
    start = round(start, 9)
    words = [{"word": " " + token, "start": round(start + index * step, 9),
              "end": round(start + (index + 1) * step, 9), "probability": .97,
              "custom_alignment_field": index} for index, token in enumerate(tokens)]
    return {"id": identifier, "start": start, "end": words[-1]["end"],
            "text": _words_text(words), "words": words,
            **({"asr_recovered": True} if recovered else {})}


def source(*segments):
    return {"duration": max(s["end"] for s in segments) + 1, "language": "en", "segments": list(segments)}


def assemble(transcript, **options):
    original = deepcopy(transcript)
    result = _classification_transcript(validate_transcript(transcript), **options)
    assert transcript == original
    assert [w for s in result["segments"] for w in s.get("words", [])] == [
        w for s in transcript["segments"] for w in s.get("words", [])]
    assert [s["id"] for s in result["segments"]] == list(range(len(result["segments"])))
    assert all(right["start"] >= left["end"] for left, right in zip(result["segments"], result["segments"][1:]))
    return result["segments"]


def test_touching_fragments_join_without_changing_display_text_or_word_metadata():
    first = segment(10, ["One", "natural"], start=1)
    middle = segment(20, ["sentence", "continues"], start=first["end"])
    last = segment(30, ["across", "segments."], start=middle["end"])
    first["text"] = "Original display formatting stays untouched"
    units = assemble(source(first, middle, last))
    assert len(units) == 1
    assert units[0]["text"] == "One natural sentence continues across segments."
    assert (units[0]["start"], units[0]["end"]) == (first["words"][0]["start"], last["words"][-1]["end"])


@pytest.mark.parametrize("gap", [.00001, .02, 1.])
def test_positive_segment_gap_never_becomes_part_of_a_joined_unit(gap):
    first = segment(0, ["Unfinished"])
    second = segment(1, ["continuation."], start=first["end"] + gap)
    units = assemble(source(first, second))
    assert len(units) == 2
    assert units[1]["start"] - units[0]["end"] == pytest.approx(gap)


def test_touching_segment_bounds_do_not_hide_an_alignment_gap():
    first = segment(0, ["Unfinished"], step=1)
    first["words"][-1]["end"] = .8
    second = segment(1, ["continuation."], start=1)
    units = assemble(source(first, second))
    assert [(u["start"], u["end"]) for u in units] == [(0, .8), (1, 1.1)]


@pytest.mark.parametrize("ending", ["done.", "done!", "done?”", "句子。』"])
def test_sentence_punctuation_remains_a_hard_boundary(ending):
    first = segment(0, [ending])
    second = segment(1, ["Next."], start=first["end"])
    assert len(assemble(source(first, second))) == 2


def test_abbreviation_can_continue_across_a_touching_segment_boundary():
    first = segment(0, ["Dr."])
    second = segment(1, ["Smith", "speaks."], start=first["end"])
    assert assemble(source(first, second))[0]["text"] == "Dr. Smith speaks."


def test_recovery_flags_are_ored_only_over_words_in_each_unit():
    first = segment(0, ["Ordinary"])
    recovered = segment(1, ["recovered", "speech."], start=first["end"], recovered=True)
    recovered["words"][0]["probability"] = .2
    last = segment(2, ["Editorial."], start=recovered["end"])
    units = assemble(source(first, recovered, last))
    assert [bool(unit.get("asr_recovered")) for unit in units] == [True, False]
    assert units[0]["words"][1]["probability"] == .2


def test_unaligned_and_zero_duration_alignment_form_preserved_barriers():
    first = segment(0, ["Before"], start=1)
    unaligned = {"id": 1, "start": 1.1, "end": 2, "text": "No alignment"}
    zero = {"id": 2, "start": 2, "end": 3, "text": "Zero aligned speech",
            "words": [{"word": "Zero aligned speech", "start": 2, "end": 2}], "asr_recovered": True}
    last = segment(3, ["After."], start=3)
    units = assemble(source(first, unaligned, zero, last))
    assert len(units) == 4
    assert units[1] == unaligned
    assert units[2] == zero


def test_zero_duration_sentence_rolls_back_segment_without_losing_prior_words():
    first = segment(0, ["First"], step=.5)
    second = {"id": 1, "start": .5, "end": 1.5, "text": "continuation. Zero. Tail",
              "words": [{"word": " continuation.", "start": .5, "end": .8},
                        {"word": " Zero.", "start": .8, "end": .8},
                        {"word": " Tail", "start": .8, "end": 1.5}]}
    last = segment(2, ["Next."], start=1.5)
    units = assemble(source(first, second, last))
    assert len(units) == 3
    assert units[1] == second


def test_zero_duration_tail_rolls_back_segment_and_preserves_pending_sentence():
    first = segment(0, ["First"], step=.5)
    second = {"id": 1, "start": .5, "end": 1.5, "text": "continuation. Untimed tail",
              "words": [{"word": " continuation.", "start": .5, "end": .8},
                        {"word": " Untimed tail", "start": .8, "end": .8}]}
    last = segment(2, ["Next."], start=1.5)
    units = assemble(source(first, second, last))
    assert [(u["start"], u["end"]) for u in units] == [(0, .5), (.5, 1.5), (1.5, 1.6)]
    assert units[1] == second


@pytest.mark.parametrize("tokens,step,expected_count", [
    (["x"] * 201, .1, 3),
    (["x" * 40] * 40, .1, 3),
    (["x"] * 40, 1, 2),
])
def test_size_caps_split_long_unpunctuated_speech_without_dropping_words(tokens, step, expected_count):
    units = assemble(source(segment(0, tokens, step=step)))
    assert len(units) == expected_count
    assert all(len(u["words"]) <= 100 and len(u["text"]) <= 800 and u["end"] - u["start"] <= 30 for u in units)


@pytest.mark.parametrize("tokens,step", [(["x" * 801], .1), (["x"], 31)])
def test_unsplittable_oversized_word_is_rejected_without_inventing_alignment(tokens, step):
    transcript = source(segment(0, tokens, step=step))
    original = deepcopy(transcript)
    with pytest.raises(ValueError, match="finer word alignment"):
        _classification_transcript(transcript)
    assert transcript == original


def test_small_configured_ai_window_uses_compatible_sentence_units(monkeypatch):
    monkeypatch.setenv("CASTWELL_AI_WINDOW_CHARS", "256")
    transcript = source(segment(0, ["ordinary"] * 80))
    original = deepcopy(transcript)
    with patch("castwell.ad_review.classify_verified", return_value=[]) as classifier:
        assert detect_ads(transcript, "ai", config={"ai_policy": "verified",
            "ai_base_url": "http://127.0.0.1:8081/v1", "ai_model": "mock"}) == []
    units = classifier.call_args.args[0]["segments"]
    assert len(units) == 3
    assert all(len(unit["text"]) <= 256 for unit in units)
    assert [w for unit in units for w in unit["words"]] == transcript["segments"][0]["words"]
    assert transcript == original
