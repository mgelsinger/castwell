"""Saved-cut audits test interval semantics independently of the detector."""
import importlib.util
import copy
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

spec = importlib.util.spec_from_file_location("saved_cut_audit", Path(__file__).resolve().parents[1] / "scripts/audit_saved_cuts.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def fixture(words, labels, cuts):
    transcript = {"duration": 12, "segments": [{"words": [{"word": "word", "start": a, "end": b} for a, b in words]}]}
    decisions = {"source_word_count": len(words), "word_decisions": {str(i): value for i, value in enumerate(labels)}}
    proposals = [{"start": a, "end": b, "approval_metadata": {"first_word_id": first, "last_word_id": last}}
                 for a, b, first, last in cuts]
    return transcript, decisions, proposals


def test_full_word_coverage_still_exposes_unclassified_retained_gap():
    result = audit.audit_words(*fixture([(0, 1), (2, 3), (5, 6), (8, 9)],
        ["editorial", "commercial", "commercial", "editorial"], [(2, 3, 1, 1), (5, 6, 2, 2)]))
    assert result["summary"]["source_word_consistency_passed"]
    assert result["summary"]["retained_gap_seconds"] == 2
    assert result["retained_gaps"][0]["retained_intervals"] == [
        {"source_start": 3, "source_end": 5, "preview_start": 2, "preview_end": 4}]
    assert result["retained_gaps"][0]["semantic_status"] == "unclassified_audio_requires_listening"


def test_overlapping_proposals_are_scored_as_union_without_double_counting():
    result = audit.audit_words(*fixture([(1, 2), (2, 4), (4, 5)], ["commercial"] * 3,
        [(1, 4, 0, 1), (2, 5, 1, 2)]))
    assert result["summary"]["cut_union_seconds"] == 4
    assert result["summary"]["commercial_word_seconds_outside_cuts"] == 0
    assert result["summary"]["overlapping_cut_seconds_before_union"] == 2
    assert result["summary"]["source_word_consistency_passed"]


def test_partial_word_and_protected_point_are_not_hidden_by_good_endpoints():
    result = audit.audit_words(*fixture([(1, 2), (3, 3), (4, 5)],
        ["commercial", "uncertain", "commercial"], [(1.5, 5, 0, 2)]))
    assert result["summary"]["commercial_word_seconds_outside_cuts"] == .5
    assert result["summary"]["commercial_word_entries_not_fully_selected"] == 1
    assert result["summary"]["noncommercial_source_word_intersections"] == 1
    assert result["summary"]["invalid_cut_boundary_count"] == 1
    assert not result["summary"]["source_word_consistency_passed"]


def test_half_open_cut_end_does_not_select_next_protected_word_point():
    result = audit.audit_words(*fixture([(1, 2), (2, 2)], ["commercial", "editorial"], [(1, 2, 0, 0)]))
    assert result["summary"]["noncommercial_source_word_intersections"] == 0
    assert result["summary"]["source_word_consistency_passed"]


def test_unselected_zero_duration_commercial_point_cannot_pass_consistency():
    result = audit.audit_words(*fixture([(1, 2), (2, 2)], ["commercial", "commercial"], [(1, 2, 0, 0)]))
    assert result["summary"]["commercial_word_entries_not_fully_selected"] == 0
    assert result["summary"]["invalid_cut_boundary_count"] == 0
    assert result["summary"]["zero_duration_commercial_points_outside_cuts"] == 1
    assert not result["summary"]["source_word_consistency_passed"]


@pytest.mark.parametrize("mapping", [{"0": "commercial"}, {"0": "commercial", "2": "editorial"},
                                    {"0": "commercial", "1": "unknown"}])
def test_incomplete_or_malformed_decision_identity_is_rejected(mapping):
    transcript, decisions, cuts = fixture([(1, 2), (3, 4)], ["commercial", "editorial"], [(1, 2, 0, 0)])
    decisions["word_decisions"] = mapping
    with pytest.raises(ValueError, match="exactly cover"):
        audit.audit_words(transcript, decisions, cuts)


def test_declared_word_boundary_cannot_use_boolean_or_out_of_range_identity():
    transcript, decisions, cuts = fixture([(1, 2)], ["commercial"], [(1, 2, True, 5)])
    result = audit.audit_words(transcript, decisions, cuts)
    assert result["summary"]["invalid_cut_boundary_count"] == 1
    assert not result["summary"]["source_word_consistency_passed"]


def test_overlapping_source_word_mapping_is_rejected():
    with pytest.raises(ValueError, match="nonoverlapping"):
        audit.audit_words(*fixture([(1, 3), (2, 4)], ["commercial", "commercial"], [(1, 4, 0, 1)]))


def test_declared_timeline_match_does_not_hide_extra_decoded_source_tail():
    summary = {"source_duration_seconds": 12, "cut_count": 1, "cut_union_seconds": 2,
               "intended_retained_duration_seconds": 10}
    source = {"sample_rate": 48000, "decoded_seconds": 12.024, "container_duration_seconds": 12}
    preview = {"sample_rate": 48000, "decoded_seconds": 10, "container_duration_seconds": 10}
    result = audit.compare_durations(summary, source, preview, 10)
    assert result["preview_decode_matches_declared_edit_timeline"]
    assert not result["decoded_duration_within_sample_boundary_tolerance"]
    assert result["source_decoded_minus_declared_duration_seconds"] == pytest.approx(.024)
    assert result["decoded_duration_error_seconds"] == pytest.approx(-.024)


def test_render_duration_error_is_not_accepted_as_codec_padding():
    summary = {"source_duration_seconds": 12, "cut_count": 1, "cut_union_seconds": 2,
               "intended_retained_duration_seconds": 10}
    source = {"sample_rate": 48000, "decoded_seconds": 12, "container_duration_seconds": 12}
    preview = {"sample_rate": 48000, "decoded_seconds": 10.02, "container_duration_seconds": 10.02}
    result = audit.compare_durations(summary, source, preview, 10)
    assert not result["preview_decode_matches_declared_edit_timeline"]
    assert not result["decoded_duration_within_sample_boundary_tolerance"]
    assert not result["benchmark_render_duration_matches"]


@pytest.mark.parametrize("malformation", ["segments", "segment", "word", "approval_metadata"])
def test_malformed_nested_source_or_cut_objects_are_rejected(malformation):
    transcript, decisions, cuts = fixture([(1, 2)], ["commercial"], [(1, 2, 0, 0)])
    if malformation == "segments":
        transcript["segments"] = None
    elif malformation == "segment":
        transcript["segments"][0] = None
    elif malformation == "word":
        transcript["segments"][0]["words"][0] = None
    else:
        cuts[0]["approval_metadata"] = None
    with pytest.raises(ValueError):
        audit.audit_words(transcript, decisions, cuts)


PROBE = {"streams": [{"sample_rate": "48000", "channels": 2, "codec_name": "mp3"}],
         "format": {"duration": "12.0"}}


@pytest.mark.parametrize("streams", [None, [], [None]])
def test_probe_without_selected_audio_stream_is_rejected(streams):
    probe = copy.deepcopy(PROBE)
    probe["streams"] = streams
    with pytest.raises(ValueError, match="one selected stream"):
        audit.audio_metadata(probe)


@pytest.mark.parametrize("field,value", [("sample_rate", "0"), ("sample_rate", "inf"),
    ("channels", True), ("channels", 1.5), ("channels", -2), ("duration", "nan"), ("duration", "0")])
def test_nonfinite_or_nonpositive_audio_metadata_is_rejected(field, value):
    probe = copy.deepcopy(PROBE)
    target = probe["format"] if field == "duration" else probe["streams"][0]
    target[field] = value
    with pytest.raises(ValueError):
        audit.audio_metadata(probe)


@pytest.mark.parametrize("benchmark_duration", [float("nan"), 0, True])
def test_invalid_benchmark_duration_cannot_generate_comparison(benchmark_duration):
    summary = {"source_duration_seconds": 12, "cut_count": 1, "cut_union_seconds": 2,
               "intended_retained_duration_seconds": 10}
    source = {"sample_rate": 48000, "decoded_seconds": 12, "container_duration_seconds": 12}
    preview = {"sample_rate": 48000, "decoded_seconds": 10, "container_duration_seconds": 10}
    with pytest.raises(ValueError):
        audit.compare_durations(summary, source, preview, benchmark_duration)


def test_decode_has_real_wall_deadline_and_closes_temporary_files(monkeypatch):
    opened = []
    temporary_file = audit.tempfile.TemporaryFile

    def tracked_file():
        result = temporary_file()
        opened.append(result)
        return result

    monkeypatch.setattr(audit.tempfile, "TemporaryFile", tracked_file)
    command = [sys.executable, "-c", "import os,time;os.write(1,b'a'*1048576);os.write(2,b'b'*1048576);time.sleep(30)"]
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        audit.decoded_pcm_size(command, timeout=.5)
    assert time.monotonic() - start < 10
    assert len(opened) == 2 and all(stream.closed for stream in opened)


def test_disk_backed_decode_counts_all_pcm_bytes():
    count = audit.decoded_pcm_size([sys.executable, "-c", "import os;os.write(1,b'a'*1048576)"], timeout=10)
    assert count == 1048576


def test_empty_or_incomplete_pcm_output_is_rejected(monkeypatch):
    monkeypatch.setattr(audit.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, stdout=json.dumps(PROBE).encode()))
    for byte_count in (0, 3):
        monkeypatch.setattr(audit, "decoded_pcm_size", lambda *args, **kwargs: byte_count)
        with pytest.raises(ValueError, match="invalid sample count"):
            audit.decode_evidence(Path("source.mp3"), "ffmpeg", "ffprobe")


@pytest.mark.parametrize("error_type", [AttributeError, IndexError])
def test_cli_fallback_never_prints_private_input_exception(monkeypatch, capsys, error_type):
    def malformed_input():
        raise error_type("private-path-and-transcript-must-not-escape")
    monkeypatch.setattr(audit, "main", malformed_input)
    assert audit.cli() == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "failed"
    assert "private-path-and-transcript" not in output.out + output.err
