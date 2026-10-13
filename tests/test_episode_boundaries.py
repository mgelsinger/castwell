"""Source ownership and whole-scene boundary contracts, without model calls."""

from copy import deepcopy

import pytest

from castwell.episode_boundaries import (BoundaryReviewError, build_boundary_plan,
                                         pack_boundary_requests, parse_word_partition,
                                         refine_boundaries)


def source(labels):
    units = []
    for index, _ in enumerate(labels):
        words = [{"word": f" Source{index}", "start": float(index * 2),
                  "end": index * 2 + .8, "probability": .99},
                 {"word": " sentence.", "start": index * 2 + .8,
                  "end": float(index * 2 + 1), "probability": .98}]
        units.append({"id": index, "start": words[0]["start"], "end": words[-1]["end"],
                      "text": f"Source{index} sentence.", "words": words})
    return units, dict(enumerate(labels))


def test_plan_keeps_complete_scenes_and_editorial_holes_without_retargeting_them():
    units, labels = source(["editorial"] * 3 + ["commercial"] * 6 + ["editorial"]
                           + ["commercial"] * 2 + ["editorial"] * 3)
    original = deepcopy(units)
    plan = build_boundary_plan(units, labels)
    left, right = plan["scene_blocks"]
    assert left["scene_unit_ids"] == list(range(3, 9))
    assert left["target_unit_ids"] == [3, 4, 7, 8]
    assert left["context_unit_ids"] == list(range(1, 10))
    assert right["scene_unit_ids"] == [10, 11]
    assert right["context_unit_ids"] == list(range(9, 14))
    assert plan["requests"][0]["scene_ids"] == [0, 1]
    assert "SOURCE UNIT 5, SCENE CONTEXT: Source5 sentence." in plan["requests"][0]["scene_context"]
    assert "SOURCE UNIT 9, PROTECTED CONTEXT: Source9 sentence." in plan["requests"][0]["scene_context"]
    assert all(labels[plan["words"][i]["parent_id"]] != "editorial"
               for i in plan["target_word_ids"])
    assert units == original
    assert [row["word"] for row in plan["words"]] == [w for u in units for w in u["words"]]


def test_mixed_and_uncertain_interior_units_are_always_targeted():
    units, labels = source(["commercial"] * 8)
    labels[3], labels[4] = "mixed", "uncertain"
    plan = build_boundary_plan(units, labels)
    assert plan["scene_blocks"][0]["target_unit_ids"] == [0, 1, 3, 4, 6, 7]
    assert plan["defaults"][6] == plan["defaults"][8] == "uncertain"
    assert plan["defaults"][4] == "commercial"


def test_word_metadata_zero_duration_and_source_are_preserved():
    units, labels = source(["commercial"])
    units[0]["words"][0]["end"] = units[0]["words"][0]["start"]
    units[0]["words"][0]["original_timestamp"] = [1.5, 1.5]
    units[0]["asr_recovered"] = True
    original = deepcopy(units)
    plan = build_boundary_plan(units, labels)
    assert plan["words"][0]["start"] == plan["words"][0]["end"] == 0
    assert plan["words"][0]["asr_recovered"] is True
    plan["words"][0]["word"]["original_timestamp"][0] = 9
    assert units == original


def test_all_editorial_needs_no_boundary_request():
    units, labels = source(["editorial", "editorial"])
    plan = build_boundary_plan(units, labels)
    def forbidden(_):
        pytest.fail("An editorial-only episode must not be reclassified")
    decisions, audit = refine_boundaries(plan, request=forbidden, fits=forbidden)
    assert decisions == {0: "editorial", 1: "editorial", 2: "editorial", 3: "editorial"}
    assert audit["requests"] == audit["target_word_ids"] == []


def test_packing_changes_only_scene_boundaries_and_renumbers_dense_targets():
    units, labels = source(["commercial", "editorial", "commercial", "editorial", "commercial"])
    plan = build_boundary_plan(units, labels)
    batches = pack_boundary_requests(plan, lambda request: len(request["scene_ids"]) <= 2)
    assert [row["scene_ids"] for row in batches] == [[0, 1], [2]]
    assert [row["id"] for row in batches[1]["units"]] == [0, 1]
    assert batches[1]["source_word_ids"] == [8, 9]
    assert [i for row in batches for i in row["source_word_ids"]] == plan["target_word_ids"]
    assert "Source4" in batches[1]["scene_context"]


def test_indivisible_oversized_scene_fails_before_any_inference():
    units, labels = source(["commercial", "editorial", "commercial"])
    plan = build_boundary_plan(units, labels)
    calls = []
    with pytest.raises(BoundaryReviewError, match="Complete scene 1"):
        refine_boundaries(plan, request=lambda row: calls.append(row),
                          fits=lambda row: row["scene_ids"] == [0])
    assert calls == []


def test_refinement_maps_local_words_exactly_and_protects_global_interiors():
    units, labels = source(["editorial"] + ["commercial"] * 6 + ["editorial"])
    plan = build_boundary_plan(units, labels)
    original_plan = deepcopy(plan)
    def request(block):
        # The first two source words are the neutral publisher tagline.
        block["units"][0]["word"]["word"] = "callback mutation must not alter source"
        return {"ranges": [{"end_id": 1, "label": "editorial"},
                           {"end_id": len(block["units"]) - 1, "label": "commercial"}]}
    decisions, audit = refine_boundaries(plan, request=request, fits=lambda _: True)
    assert decisions[0] == decisions[1] == decisions[2] == decisions[3] == "editorial"
    assert decisions[6] == decisions[7] == "commercial"  # Untargeted scene interior.
    assert decisions[14] == decisions[15] == "editorial"
    assert set(audit["word_votes"]) == set(plan["target_word_ids"])
    assert len(audit["requests"][0]["request_sha256"]) == 64
    assert plan == original_plan


def test_uncertain_boundary_result_remains_unresolved():
    units, labels = source(["mixed"])
    plan = build_boundary_plan(units, labels)
    decisions, audit = refine_boundaries(plan, request=lambda _: {0: "commercial", 1: "uncertain"},
                                         fits=lambda _: True)
    assert decisions == {0: "commercial", 1: "uncertain"}
    assert audit["unresolved_word_ids"] == [1]


def test_invalid_output_never_becomes_a_complete_decision_map():
    bad_answers = [
        {}, {0: "commercial"}, {0: "commercial", 1: "commercial", 2: "editorial"},
        {False: "commercial", 1: "commercial"}, {0: "mixed", 1: "commercial"},
        {"ranges": [{"end_id": 0, "label": "commercial"}]},
        {"ranges": [{"end_id": 1, "label": "mixed"}]},
        {"ranges": [{"end_id": True, "label": "commercial"}]},
        {"ranges": [{"end_id": 1, "label": "commercial", "confidence": 1}]},
        {"ranges": [{"end_id": 1, "label": "commercial"}, {"end_id": 1, "label": "editorial"}]},
    ]
    for answer in bad_answers:
        with pytest.raises(BoundaryReviewError):
            parse_word_partition(answer, [0, 1])


def test_incomplete_global_labels_or_missing_alignment_cannot_be_planned():
    units, labels = source(["commercial", "editorial"])
    with pytest.raises(BoundaryReviewError):
        build_boundary_plan(units, {0: "commercial"})
    with pytest.raises(BoundaryReviewError):
        build_boundary_plan(units, {False: "commercial", 1: "editorial"})
    units[1]["words"] = []
    with pytest.raises(BoundaryReviewError):
        build_boundary_plan(units, labels)


def test_token_preflight_is_required_and_cannot_mutate_the_request():
    units, labels = source(["commercial"])
    plan = build_boundary_plan(units, labels)
    original = deepcopy(plan)
    def preflight(row):
        row["units"].clear()
        return True
    result = pack_boundary_requests(plan, preflight)
    assert len(result[0]["units"]) == 2
    assert plan == original
    with pytest.raises(TypeError):
        pack_boundary_requests(plan, None)
    with pytest.raises(TypeError):
        pack_boundary_requests(plan, lambda _: 32768)
