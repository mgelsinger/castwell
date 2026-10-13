"""Omission warnings are source-backed review triggers, never ad decisions."""

from copy import deepcopy

import pytest

from castwell.episode_omissions import VERSION, omission_guard


def source(texts):
    return [{"id": i, "start": i * 5., "end": i * 5. + 4., "text": text,
             "words": [{"word": text, "start": i * 5., "end": i * 5. + 4.}]}
            for i, text in enumerate(texts)]


def test_missing_sponsorship_and_transactional_offer_are_audited_without_relabeling():
    units = source(["This episode is brought to you by Example Premium.",
                    "A product with useful features.", "Sign up now for your free trial.",
                    "Terms and conditions apply."])
    labels = dict.fromkeys(range(4), "editorial")
    original = deepcopy((units, labels))
    audit = omission_guard(units, labels)
    assert audit == {"version": VERSION, "flagged_unit_ids": [0, 2, 3],
                     "cue_clusters": [{"unit_ids": [0, 1, 2, 3], "matched_unit_ids": [0, 2, 3],
                                       "cue_families": ["action_with_offer", "explicit_sponsorship"],
                                       "start": 0., "end": 19.}], "guard_requires_review": True}
    assert (units, labels) == original


def test_already_commercial_read_and_neighbors_do_not_trigger_omission():
    units = source(["We'll be right back.", "Sign up now for your free trial.",
                    "Guaranteed human."])
    audit = omission_guard(units, {0: "editorial", 1: "commercial", 2: "editorial"})
    assert audit["guard_requires_review"] is False
    assert audit["cue_clusters"] == audit["flagged_unit_ids"] == []


def test_free_podcast_promo_and_ordinary_product_discussion_are_not_sufficient_cues():
    units = source(["Join us for a new episode every Tuesday.",
                    "Download this free podcast wherever you listen.",
                    "We use code to study the company's products and save files.",
                    "This episode is not sponsored by the company we are discussing."])
    assert omission_guard(units, dict.fromkeys(range(4), "editorial"))["guard_requires_review"] is False


def test_explicit_sponsorship_parody_warns_but_preserves_its_editorial_decision():
    units = source(["Let's perform an unpaid fake advertisement for impossible vegetables.",
                    "This episode is sponsored by imaginary rutabaga bonds.",
                    "That was a joke, of course."])
    labels = dict.fromkeys(range(3), "editorial")
    original = deepcopy((units, labels))
    result = omission_guard(units, labels)
    assert result["guard_requires_review"] is True
    assert result["flagged_unit_ids"] == [1]
    assert set(result) == {"version", "flagged_unit_ids", "cue_clusters", "guard_requires_review"}
    assert (units, labels) == original


def test_coupon_code_flags_without_a_brand_allowlist_but_plain_programming_code_does_not():
    units = source(["Enter promo code SOMETHING.", "We use code to edit audio."])
    audit = omission_guard(units, {0: "editorial", 1: "editorial"})
    assert audit["flagged_unit_ids"] == [0]
    assert audit["cue_clusters"][0]["cue_families"] == ["offer_code"]


def test_uncertain_and_mixed_labels_are_already_unresolved_not_editorial_omissions():
    units = source(["This episode is brought to you by a sponsor."] * 2)
    assert omission_guard(units, {0: "uncertain", 1: "mixed"})["guard_requires_review"] is False


def test_distant_units_cannot_create_an_offer_pair_or_one_cluster():
    units = source(["Sign up today.", "Free trial.", "This show is sponsored by Example."])
    units[1].update(start=200., end=204.)
    units[2].update(start=400., end=404.)
    audit = omission_guard(units, dict.fromkeys(range(3), "editorial"))
    assert audit["flagged_unit_ids"] == [2]


def test_guard_rejects_missing_or_invented_global_ownership():
    units = source(["Ordinary discussion."])
    for labels in ({}, {False: "editorial"}, {0: "editorial", 1: "editorial"}, {0: "other"}):
        with pytest.raises(ValueError):
            omission_guard(units, labels)
