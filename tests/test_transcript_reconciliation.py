"""Anchored repair retains existing words and refuses ambiguous boundaries."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from castwell.processing import validate_transcript
from castwell.transcript_quality import recover_transcript_gaps
from castwell.transcript_reconciliation import apply_reconciliations, plan_reconciliation, reconcile_words


def word(text, start, end):
    return {"word": text, "start": start, "end": end, "probability": .95}


def source():
    return validate_transcript({"duration": 12, "language": "en", "segments": [
        {"id": 0, "start": 0, "end": 3.2, "text": "Intro. Before the break.", "words": [
            word("Intro.", 0, 1), word(" Before", 2, 2.4), word(" the", 2.4, 2.8), word(" break.", 2.8, 3.2)]},
        {"id": 1, "start": 9, "end": 12, "text": "Back to science. Next.", "words": [
            word(" Back", 9, 9.4), word(" to", 9.4, 9.8), word(" science.", 9.8, 10.2), word(" Next.", 10.3, 12)]},
    ]})


def serial(plan):
    values = [word(" before", 2.02, 2.42), word(" the", 2.42, 2.82), word(" break", 2.82, 3.22),
              word(" Buy", 4, 5), word(" something.", 5, 6), word(" Back", 9.4, 9.6),
              word(" to", 9.6, 9.9), word(" science", 9.9, 10.25)]
    return [SimpleNamespace(no_speech_prob=.1, avg_logprob=-.2, compression_ratio=1.2,
             words=[SimpleNamespace(**dict(w, start=w["start"] - plan["crop_start"],
                                            end=w["end"] - plan["crop_start"])) for w in values])]


def test_repair_retains_exact_source_text_and_maps_every_shifted_word():
    original = source()
    frozen = deepcopy(original)
    plan = plan_reconciliation(original, 3.2, 9)
    replacement, reason = reconcile_words(original, plan, serial(plan))
    assert reason == "reconciled"
    result = apply_reconciliations(original, [replacement])
    assert original == frozen
    assert result["segments"][0]["words"] == original["segments"][0]["words"][:1]
    assert result["segments"][-1]["words"] == original["segments"][-1]["words"][-1:]
    repaired = result["segments"][1]
    assert [w["word"] for w in repaired["words"]] == [
        " Before", " the", " break.", " Buy", " something.", " Back", " to", " science."]
    assert replacement["added_words"] == 2
    assert len(replacement["original_word_mapping"]) == 6
    assert replacement["original_word_mapping"][3]["original"]["start"] == 9
    assert replacement["original_word_mapping"][3]["start"] == pytest.approx(9.4)
    assert repaired["requires_review"] and repaired["asr_recovered"] and repaired["asr_reconciled"]
    assert replacement["human_listening_verified"] is False


def test_ambiguous_or_missing_anchors_never_replace_source():
    original = source()
    plan = plan_reconciliation(original, 3.2, 9)
    rows = serial(plan)
    rows[0].words[3:3] = deepcopy(rows[0].words[:3])
    replacement, reason = reconcile_words(original, plan, rows)
    assert replacement is None and reason == "missing_ambiguous_or_unordered_anchors"
    rows = serial(plan)
    rows[0].words = rows[0].words[:-3]
    assert reconcile_words(original, plan, rows)[0] is None


def test_repair_cannot_move_words_into_untouched_neighbor_or_keep_uncertain_insert():
    original = source()
    plan = plan_reconciliation(original, 3.2, 9)
    rows = serial(plan)
    rows[0].words[-1].end = 10.4 - plan["crop_start"]
    assert reconcile_words(original, plan, rows)[1] == "outside_untouched_neighbor_bounds"
    rows = serial(plan)
    rows[0].words[3].probability = .2
    assert reconcile_words(original, plan, rows)[1] == "uncertain_or_overlapping_serial_words"


def test_one_sided_or_wordless_source_is_not_reconciled():
    original = source()
    assert plan_reconciliation(original, 0, 1) is None
    assert plan_reconciliation(original, 11, 12) is None
    original["segments"].insert(1, {"id": 2, "start": 4, "end": 5, "text": "Unaligned original text."})
    assert plan_reconciliation(original, 3.2, 9) is None


def test_recovery_uses_one_cuda_crop_for_containment_and_guarded_alignment(monkeypatch):
    original = source()
    plan = plan_reconciliation(original, 3.2, 9)
    crop = Mock()
    monkeypatch.setattr("castwell.transcript_quality._crop_audio", crop)
    engine = Mock()
    engine.transcribe.side_effect = lambda *args, **kwargs: (iter(serial(plan)), None)
    result = recover_transcript_gaps(Path("audio.wav"), original, engine, reconcile_boundaries=True,
                                     max_crops=32, max_audio_seconds=240, max_wall_seconds=60)
    assert engine.transcribe.call_count == crop.call_count == 1
    assert engine.transcribe.call_args.kwargs["vad_filter"] is False
    assert result["quality"]["recovery"]["attempts"][0]["status"] == "reconciled"
    assert len(result["quality"]["recovery"]["reconciliations"]) == 1
    assert result["quality"]["recovery"]["candidates"] == []
    assert result["quality"]["requires_review"]


def test_later_contained_fallback_cannot_overlap_an_earlier_retimed_anchor(monkeypatch):
    original = source()
    original["duration"] = 18
    original["segments"][1].update(end=10.2, text="Back to science.",
                                     words=original["segments"][1]["words"][:3])
    original["segments"].append({"id": 2, "start": 15, "end": 18, "text": "Future episode follows.",
                                  "words": [word(" Future", 15, 16), word(" episode", 16, 17),
                                            word(" follows.", 17, 18)]})
    original = validate_transcript(original)
    first_plan = plan_reconciliation(original, 3.2, 9)
    next_plan = plan_reconciliation(original, 10.2, 15)
    later_segments = [SimpleNamespace(no_speech_prob=.1, avg_logprob=-.2, compression_ratio=1.2,
                         words=[SimpleNamespace(**dict(value,
                             start=value["start"] - next_plan["crop_start"],
                             end=value["end"] - next_plan["crop_start"]))])
                      for value in [word(" Overlap.", 10.22, 10.4), word(" Safe.", 12, 12.3)]]
    monkeypatch.setattr("castwell.transcript_quality._crop_audio", Mock())
    engine = Mock()
    engine.transcribe.side_effect = [(iter(serial(first_plan)), None), (iter(later_segments), None)]

    result = recover_transcript_gaps(Path("audio.wav"), original, engine, reconcile_boundaries=True)

    recovery = result["quality"]["recovery"]
    assert len(recovery["reconciliations"]) == 1
    assert recovery["attempts"][1]["rejected_reconciliation_overlaps"] == 1
    assert recovery["attempts"][1]["status"] == "recovered"
    assert [candidate["text"] for candidate in recovery["candidates"]] == ["Safe."]
    assert result["quality"]["requires_review"]
    assert validate_transcript(result) == result
