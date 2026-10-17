"""Matched offline thresholds distinguish elapsed audio from word proxies."""
import importlib.util
from copy import deepcopy
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("removal_comparison", Path(__file__).resolve().parents[1] / "scripts/compare_removal_candidates.py")
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def candidate(identifier="candidate", *, elapsed=True):
    return {
        "id": identifier, "run_status": "complete", "score_scope": "rendered", "source_audio_sha256": "a" * 64, "reference_sha256": "b" * 64,
        "audio_seconds": 3600,
        "timing": {"wall_seconds": 240, "complete_pipeline": True, "fresh_transcription": True,
                   "fresh_predictions": True, "cold_models": True, "cuda_only": True,
                   "request_telemetry_complete": True, "asset_downloads": 0},
        "evidence": {"removal_scope": comparison.SCOPE, "consumed_development": False,
                     "human_listening_verified": True, "acceptance_eligible": True},
        "word_metrics": {"commercial_missed_seconds": 1, "protected_selected_seconds": 0,
                         "zero_duration_commercial_points_missed": 2, "zero_duration_protected_points_selected": 0},
        "elapsed_audio_metrics": {
            "commercial_missed_seconds": 1, "protected_selected_seconds": 0,
            "unknown_audio_seconds": 0, "unknown_audio_selected_seconds": 0,
            "whole_sponsor_reads_missed": 0, "sponsor_read_inventory_complete": True,
            "whole_podcast_promos_missed": 0, "podcast_promo_inventory_complete": True,
        } if elapsed else None,
    }


def region(start, end, label):
    return {"start": start, "end": end, "label": label}


def test_word_proxy_cannot_establish_elapsed_acceptance_even_with_good_numbers():
    value = candidate(elapsed=False)
    value["evidence"].update(consumed_development=True, human_listening_verified=False, acceptance_eligible=False)
    row = comparison.compare_candidates([value])["candidates"][0]
    assert row["timing"]["speed_target_met"]
    assert row["word_metrics"]["zero_duration_commercial_points_missed"] == 2
    assert all(item["word_proxy_checks"]["remaining_ads_within_budget"] for item in row["threshold_matrix"])
    assert all(item["elapsed_audio_checks"] is None and item["result"] == "not_verified" for item in row["threshold_matrix"])
    assert "complete_sponsor_read_coverage_unknown" in row["verification_blockers"]


def test_actual_elapsed_budget_scales_by_hour_and_show_budget_stays_per_episode():
    value = candidate()
    value["audio_seconds"] = 7200
    value["timing"]["wall_seconds"] = 600
    value["elapsed_audio_metrics"].update(commercial_missed_seconds=10, protected_selected_seconds=2)
    result = comparison.compare_candidates([value])
    row = result["candidates"][0]
    assert row["timing"]["observed_seconds_per_audio_hour"] == 300
    assert row["elapsed_audio_metrics"]["commercial_missed_seconds_per_audio_hour"] == 5
    assert [r["ad_budget_seconds_per_episode"] for r in row["threshold_matrix"]] == [10] * 4
    assert [r["result"] for r in row["threshold_matrix"]] == ["fails_declared_thresholds"] * 2 + ["verified_pass"] * 2
    assert not result["application_defaults_changed"]


def test_wholly_missed_short_sponsor_read_fails_even_below_duration_budget():
    reference = {"regions": [region(0, 2, "commercial"), region(2, 3600, "protected")],
                 "sponsor_reads": [{"start": 0, "end": 2}], "sponsor_read_inventory_complete": True}
    value = candidate()
    value["elapsed_audio_metrics"] = comparison.score_elapsed_intervals([], reference, 3600)
    row = comparison.compare_candidates([value])["candidates"][0]
    assert row["elapsed_audio_metrics"]["commercial_missed_seconds"] == 2
    assert all(r["elapsed_audio_checks"]["remaining_ads_within_budget"] and not r["no_whole_sponsor_read_missed"]
               and r["result"] == "fails_declared_thresholds" for r in row["threshold_matrix"])


def test_unlabeled_elapsed_time_is_unknown_instead_of_editorial_or_silence():
    reference = {"regions": [region(0, 10, "commercial"), region(20, 3600, "protected")],
                 "sponsor_reads": [{"start": 0, "end": 10}], "sponsor_read_inventory_complete": True}
    value = candidate()
    value["elapsed_audio_metrics"] = comparison.score_elapsed_intervals(
        [{"start": 0, "end": 10}, {"start": 12, "end": 15}, {"start": 13, "end": 15}], reference, 3600)
    row = comparison.compare_candidates([value])["candidates"][0]
    assert row["elapsed_audio_metrics"]["unknown_audio_seconds"] == 10
    assert row["elapsed_audio_metrics"]["unknown_audio_selected_seconds"] == 3
    assert row["elapsed_audio_metrics"]["protected_selected_seconds"] == 0
    assert all(item["result"] == "not_verified" for item in row["threshold_matrix"])


@pytest.mark.parametrize("change", ["consumed", "not_listened", "incomplete_inventory", "cached", "partial", "cuda_unknown"])
def test_missing_independent_evidence_blocks_otherwise_good_scores(change):
    value = candidate()
    if change == "consumed": value["evidence"]["consumed_development"] = True
    elif change == "not_listened": value["evidence"]["human_listening_verified"] = None
    elif change == "incomplete_inventory": value["elapsed_audio_metrics"]["sponsor_read_inventory_complete"] = False
    elif change == "cached": value["timing"]["fresh_predictions"] = False
    elif change == "partial": value["timing"]["complete_pipeline"] = False
    else: value["timing"]["cuda_only"] = None
    row = comparison.compare_candidates([value])["candidates"][0]
    assert all(item["result"] == "not_verified" for item in row["threshold_matrix"])


def test_verified_interval_case_and_union_scoring():
    reference = {"regions": [region(0, 10, "commercial"), region(10, 3600, "protected")],
                 "sponsor_reads": [{"start": 0, "end": 10}], "sponsor_read_inventory_complete": True,
                 "podcast_promos": [], "podcast_promo_inventory_complete": True}
    value = candidate()
    value["elapsed_audio_metrics"] = comparison.score_elapsed_intervals(
        [{"start": 0, "end": 7}, {"start": 5, "end": 9}], reference, 3600)
    row = comparison.compare_candidates([value])["candidates"][0]
    assert row["elapsed_audio_metrics"]["commercial_missed_seconds"] == 1
    assert all(item["result"] == "verified_pass" for item in row["threshold_matrix"])


@pytest.mark.parametrize("change", ["source", "reference", "duration", "duplicate_id"])
def test_unmatched_candidates_cannot_be_ranked_as_equal_tests(change):
    first, second = candidate("one"), candidate("two")
    if change == "source": second["source_audio_sha256"] = "c" * 64
    elif change == "reference": second["reference_sha256"] = "c" * 64
    elif change == "duration": second["audio_seconds"] = 3599
    else: second["id"] = "one"
    with pytest.raises(ValueError, match="matching"):
        comparison.compare_candidates([first, second])


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, True])
def test_nonfinite_negative_or_boolean_thresholds_rejected(invalid):
    with pytest.raises(ValueError):
        comparison.compare_candidates([candidate()], max_ad_seconds_per_hour=invalid)


def test_complete_inventory_cannot_skip_a_commercial_region():
    reference = {"regions": [region(0, 10, "commercial"), region(10, 3600, "protected")],
                 "sponsor_reads": [{"start": 0, "end": 5}], "sponsor_read_inventory_complete": True}
    with pytest.raises(ValueError, match="every commercial"):
        comparison.score_elapsed_intervals([], reference, 3600)


def benchmark_report():
    report = {"status": "complete", "audio_seconds": 3600, "elapsed_seconds": 250, "transcript_reused": False,
              "reference": {"source_audio_sha256": "a" * 64, "reference_sha256": "b" * 64,
                            "removal_scope": comparison.SCOPE, "consumed_development": True, "acceptance_eligible": False},
              "timing": {"complete_pipeline": True, "no_transcript_or_prediction_reuse": True},
              "runtime_evidence": {"models_cold_at_start": True, "cuda_only_verified": True,
                                   "transcript_cache_hits": 0, "prediction_cache_hits": 0,
                                   "request_telemetry_complete": True, "asset_downloads": 0},
              "scores": {"candidates": {"categories": {
                  "commercial": {"word_seconds_unselected": 1.31, "zero_duration_word_entries": 3, "zero_duration_points_selected": 1},
                  "protected": {"word_seconds_selected": 0, "zero_duration_points_selected": 0}}}}}
    report["scores"]["rendered"] = deepcopy(report["scores"]["candidates"])
    report["render_mode"] = "candidate_preview"
    return report


def test_benchmark_import_preserves_proxy_and_does_not_invent_missing_evidence():
    report = benchmark_report()
    row = comparison.compare_candidates([comparison.from_benchmark(report, "baseline")])["candidates"][0]
    assert row["word_metrics"]["commercial_missed_seconds"] == 1.31
    assert row["word_metrics"]["zero_duration_commercial_points_missed"] == 2
    assert row["elapsed_audio_metrics"] is None
    assert row["timing"]["speed_target_met"]
    assert row["evidence"]["human_listening_verified"] is None


def test_cli_uses_allowlist_and_preserves_previous_outputs(tmp_path):
    value = candidate()
    value["private_transcript"] = "Never publish this source passage"
    manifest = tmp_path / "input.json"
    manifest.write_text(json.dumps({"schema_version": 1, "candidates": [value]}))
    output = tmp_path / "output.json"
    comparison.main(["--manifest", str(manifest), "--output", str(output)])
    contents = output.read_text()
    assert "Never publish" not in contents and str(tmp_path) not in contents
    with pytest.raises(FileExistsError):
        comparison.main(["--manifest", str(manifest), "--output", str(output)])
    assert output.read_text() == contents


def test_failed_benchmark_remains_visible_without_scores_or_successful_timing():
    report = {"status": "failed", "audio_seconds": 3600, "elapsed_seconds": 15,
              "reference": {"source_audio_sha256": "a" * 64, "reference_sha256": "b" * 64,
                            "removal_scope": comparison.SCOPE, "consumed_development": True},
              "runtime_evidence": {"models_cold_at_start": True, "cuda_only_verified": True},
              "error": {"message": "Never publish private failure content"}}
    row = comparison.compare_candidates([comparison.from_benchmark(report, "failed")])["candidates"][0]
    assert row["run_status"] == "failed" and row["word_metrics"] is None
    assert row["timing"]["speed_target_met"] is None
    assert all(item["result"] == "run_failed" for item in row["threshold_matrix"])
    assert "Never publish" not in json.dumps(row)


def test_frozen_parody_control_uses_existing_ids_and_never_copies_text():
    report = {"status": "complete", "reference": {"source_audio_sha256": "a" * 64, "reference_sha256": "b" * 64,
                                                   "removal_scope": comparison.SCOPE},
              "scores": {"rendered": {"errors": {"protected_selected": [
                  {"global_word_index": 5, "start": 10, "end": 11, "selected_seconds": .5, "word": "private source"}]}}}}
    reference = {"source_sha256": "a" * 64, "removal_scope": comparison.SCOPE,
                 "protected": [{"id": "protected-02", "role": "unpaid_parody",
                                "global_word_index_range_inclusive": [4, 6], "word_seconds": 2.5}]}
    controls = comparison.protected_controls_from_reference(report, reference, "b" * 64)
    assert controls[0]["selected_word_entries"] == 1 and controls[0]["selected_word_seconds"] == .5
    assert controls[0]["reference_role"] == "reference_labeled_parody"
    assert "private source" not in json.dumps(controls)
    with pytest.raises(ValueError, match="frozen"):
        comparison.protected_controls_from_reference(report, reference, "c" * 64)


def test_completed_pipeline_cannot_claim_zero_elapsed_wall_time():
    value = candidate()
    value["timing"]["wall_seconds"] = 0
    with pytest.raises(ValueError, match="positive elapsed"):
        comparison.compare_candidates([value])


def test_incomplete_telemetry_or_download_accounting_cannot_pass_imported_speed():
    for key, value in [("request_telemetry_complete", False), ("request_telemetry_complete", None),
                       ("asset_downloads", 1), ("asset_downloads", None)]:
        report = benchmark_report()
        report["runtime_evidence"][key] = value
        row = comparison.compare_candidates([comparison.from_benchmark(report, "baseline")])["candidates"][0]
        assert row["timing"]["speed_target_met"] is None
        assert not row["timing"]["eligible_for_speed_gate"]


def test_actual_rendered_scores_replace_better_proposals_and_missing_render_is_unknown():
    report = benchmark_report()
    report["render_mode"] = "approved_cuts"
    report["scores"]["rendered"]["categories"]["commercial"]["word_seconds_unselected"] = 100
    row = comparison.compare_candidates([comparison.from_benchmark(report, "subset")])["candidates"][0]
    assert row["score_scope"] == "rendered" and row["render_mode"] == "approved_cuts"
    assert row["word_metrics"]["commercial_missed_seconds"] == 100
    assert all(not result["word_proxy_thresholds_met"] for result in row["threshold_matrix"])
    del report["scores"]["rendered"]
    row = comparison.compare_candidates([comparison.from_benchmark(report, "unknown")])["candidates"][0]
    assert row["score_scope"] == "unknown" and row["word_metrics"] is None
    assert "rendered_output_score_not_established" in row["verification_blockers"]


@pytest.mark.parametrize("arguments", [["--unknown-private-option", "private-value"],
                                       ["--max-ad-seconds-per-hour", "private-value"]])
def test_argparse_errors_do_not_echo_values_or_unknown_options(arguments, capsys):
    with pytest.raises(SystemExit) as error:
        comparison.main(["--output", "unused-output.json", *arguments])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert "private" not in captured.out + captured.err
    assert json.loads(captured.err)["status"] == "failed"


@pytest.mark.parametrize("scope", [None, "undeclared-other-policy", True, {"scope": "private"}])
def test_candidate_scope_must_be_explicit_supported_identity(scope):
    value = candidate()
    value["evidence"]["removal_scope"] = scope
    with pytest.raises(ValueError, match="explicit removal scope"):
        comparison.compare_candidates([value])


def test_current_and_historical_scopes_cannot_be_compared_as_equal_tests():
    current, historical = candidate("current"), candidate("historical")
    historical["evidence"]["removal_scope"] = comparison.HISTORICAL_SCOPE
    with pytest.raises(ValueError, match="matching.*removal scope"):
        comparison.compare_candidates([current, historical])


def test_historical_scope_retains_scores_and_speed_but_never_current_acceptance():
    values = [candidate("old-one"), candidate("old-two")]
    for value in values:
        value["evidence"]["removal_scope"] = comparison.HISTORICAL_SCOPE
        value["elapsed_audio_metrics"].pop("whole_podcast_promos_missed")
        value["elapsed_audio_metrics"].pop("podcast_promo_inventory_complete")
    frozen = deepcopy(values)
    result = comparison.compare_candidates(values)
    assert values == frozen
    assert result["removal_scope"] == comparison.HISTORICAL_SCOPE
    assert result["current_removal_scope"] == comparison.SCOPE
    assert result["scope_matches_current_policy"] is False
    for row in result["candidates"]:
        assert row["word_metrics"]["commercial_missed_seconds"] == 1
        assert row["timing"]["speed_target_met"] is True
        assert "historical_removal_scope_not_current_acceptance" in row["verification_blockers"]
        assert all(item["result"] == "not_verified" for item in row["threshold_matrix"])


def test_current_scope_requires_separately_declared_podcast_promo_coverage():
    value = candidate()
    for key in ("whole_podcast_promos_missed", "podcast_promo_inventory_complete"):
        value["elapsed_audio_metrics"].pop(key)
    row = comparison.compare_candidates([value])["candidates"][0]
    assert "complete_podcast_promo_coverage_unknown" in row["verification_blockers"]
    assert all(item["result"] == "not_verified" for item in row["threshold_matrix"])


def test_whole_genuine_podcast_promo_miss_fails_even_below_remaining_duration_budget():
    reference = {"regions": [region(0, 4, "commercial"), region(4, 3600, "protected")],
                 "sponsor_reads": [{"start": 0, "end": 2}], "sponsor_read_inventory_complete": True,
                 "podcast_promos": [{"start": 2, "end": 4}], "podcast_promo_inventory_complete": True}
    value = candidate()
    value["elapsed_audio_metrics"] = comparison.score_elapsed_intervals([{"start": 0, "end": 2}], reference, 3600)
    row = comparison.compare_candidates([value])["candidates"][0]
    assert row["elapsed_audio_metrics"]["whole_sponsor_reads_missed"] == 0
    assert row["elapsed_audio_metrics"]["whole_podcast_promos_missed"] == 1
    assert all(item["elapsed_audio_checks"]["remaining_ads_within_budget"]
               and item["no_whole_podcast_promo_missed"] is False
               and item["result"] == "fails_declared_thresholds" for item in row["threshold_matrix"])
    value["elapsed_audio_metrics"] = comparison.score_elapsed_intervals([{"start": 0, "end": 4}], reference, 3600)
    assert all(item["result"] == "verified_pass"
               for item in comparison.compare_candidates([value])["candidates"][0]["threshold_matrix"])


def test_complete_promo_inventory_requires_explicit_units_and_cannot_hide_uncovered_commercial():
    reference = {"regions": [region(0, 4, "commercial"), region(4, 3600, "protected")],
                 "sponsor_reads": [{"start": 0, "end": 2}], "sponsor_read_inventory_complete": True,
                 "podcast_promo_inventory_complete": True}
    with pytest.raises(ValueError, match="explicit units"):
        comparison.score_elapsed_intervals([], reference, 3600)
    reference["podcast_promos"] = [{"start": 2, "end": 3}]
    with pytest.raises(ValueError, match="every commercial"):
        comparison.score_elapsed_intervals([], reference, 3600)


def test_parody_controls_require_matching_explicit_reference_and_report_scopes():
    report = {"status": "failed", "reference": {"source_audio_sha256": "a" * 64,
              "reference_sha256": "b" * 64, "removal_scope": comparison.HISTORICAL_SCOPE}}
    reference = {"source_sha256": "a" * 64, "removal_scope": comparison.SCOPE, "protected": []}
    with pytest.raises(ValueError, match="frozen"):
        comparison.protected_controls_from_reference(report, reference, "b" * 64)
    reference["removal_scope"] = comparison.HISTORICAL_SCOPE
    assert comparison.protected_controls_from_reference(report, reference, "b" * 64) == []
