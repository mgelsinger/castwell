"""Compact scene candidate checks with mocked responses, no model inference."""

from copy import deepcopy
import json

import pytest
import requests

from castwell import processing, scene_review as scene
from castwell.ad_scope import INSTRUCTIONS


def transcript(*texts):
    return {"language": "en", "duration": len(texts) * 2,
            "segments": [{"id": index, "start": index * 2, "end": (index + 1) * 2, "text": text}
                         for index, text in enumerate(texts)]}


def classify(source, **options):
    return scene.classify_scenes(source, base_url="http://127.0.0.1:8081/v1", model="fixture", **options)


class Response:
    def __init__(self, body):
        self.body, self.closed = body, False

    def json(self):
        return self.body

    def close(self):
        self.closed = True


class Provider:
    def __init__(self, labels="editorial", mutate=None):
        self.labels, self.mutate = labels, mutate
        self.calls, self.responses = [], []

    def __call__(self, url, headers, payload, *args, **kwargs):
        context = json.loads(payload["messages"][1]["content"])
        stage = "intent" if "commercial INTENT" in payload["messages"][0]["content"] else "boundaries"
        self.calls.append((stage, context, payload, headers, kwargs))
        ranges = []
        for identifier in context["target_ids"]:
            label = self.labels(stage, identifier) if callable(self.labels) else self.labels
            if ranges and ranges[-1]["label"] == label:
                ranges[-1]["last_id"] = identifier
            else:
                ranges.append({"first_id": identifier, "last_id": identifier, "label": label,
                               "confidence": .99, "reason": "The scene establishes this role.",
                               "evidence_id": identifier})
        answer = {"ranges": ranges}
        body = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}
        if self.mutate:
            replacement = self.mutate(stage, context, answer, body)
            if replacement is not None:
                body = replacement
            else:
                body["choices"][0]["message"]["content"] = json.dumps(answer)
        response = Response(body)
        self.responses.append(response)
        return response


def test_all_editorial_units_get_one_sweep_and_no_fabricated_boundary_check(monkeypatch):
    source = transcript(*[f"Editorial sentence {index}." for index in range(140)])
    original, audits = deepcopy(source), []
    provider = Provider()
    monkeypatch.setattr(processing, "_classifier_request", provider)
    assert classify(source, decision_sink=audits.append, review_only=False, ai_reasoning=True) == []
    assert source == original
    assert len(provider.calls) == 3
    assert [identifier for _, context, *_ in provider.calls for identifier in context["target_ids"]] == list(range(140))
    assert all(stage == "intent" for stage, *_ in provider.calls)
    assert all(row["boundary"] is None and not row["boundary_audited"] for row in audits[0].values())
    assert len(audits[0]) == 140
    assert all(response.closed for response in provider.responses)
    for _, _, payload, *_ in provider.calls:
        assert payload["max_tokens"] == 2048
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert "reasoning_budget_tokens" not in payload
        assert payload["response_format"]["json_schema"]["strict"] is True


def test_boundary_only_covers_candidate_blocks_and_six_neighbors(monkeypatch):
    def labels(stage, identifier):
        return "commercial" if identifier == 40 or stage == "boundaries" and identifier == 35 else "editorial"
    source = transcript(*[f"Sentence {index}." for index in range(90)])
    provider, audits = Provider(labels), []
    monkeypatch.setattr(processing, "_classifier_request", provider)
    cuts = classify(source, decision_sink=audits.append)
    targets = [identifier for stage, context, *_ in provider.calls if stage == "boundaries" for identifier in context["target_ids"]]
    assert targets == list(range(34, 47))
    assert cuts[0]["start"] == 70 and cuts[0]["end"] == 72
    assert cuts[0]["label"] == "uncertain"
    assert cuts[1]["start"] == 80 and cuts[1]["label"] == "commercial"
    assert audits[0][35]["intent"]["label"] == "editorial"
    assert audits[0][35]["boundary"]["label"] == "commercial"
    assert audits[0][33]["boundary"] is None
    assert audits[0][46]["boundary"]["label"] == "editorial"
    assert audits[0][47]["boundary"] is None
    assert all(not cut["approved"] and cut["requires_review"] for cut in cuts)


def test_overlapping_candidate_neighborhoods_are_judged_once_and_small_limits_honored(monkeypatch):
    provider = Provider(lambda stage, identifier: "uncertain" if identifier in (8, 10) else "editorial")
    monkeypatch.setattr(processing, "_classifier_request", provider)
    classify(transcript(*[f"Sentence {index}." for index in range(30)]), context_segments=2, window_chars=40)
    boundary_ids = [identifier for stage, context, *_ in provider.calls if stage == "boundaries" for identifier in context["target_ids"]]
    assert boundary_ids == list(range(6, 13))
    for _, context, *_ in provider.calls:
        assert len(context["target_ids"]) <= 64
        target_ids = set(context["target_ids"])
        context_units = context["context"]
        core_units = [unit for unit in context_units if unit["id"] in target_ids]
        left = [unit for unit in context_units if unit["id"] < min(target_ids)]
        right = [unit for unit in context_units if unit["id"] > max(target_ids)]
        assert sum(len(unit["text"]) for unit in core_units) <= 40
        assert len(left) <= 2 and len(right) <= 2
        assert sum(len(unit["text"]) for unit in left) <= 20
        assert sum(len(unit["text"]) for unit in right) <= 20


def test_wider_context_includes_future_offer_beyond_four_neighbors(monkeypatch):
    source = transcript(*[f"Sentence {index}." for index in range(80)])
    provider = Provider()
    monkeypatch.setattr(processing, "_classifier_request", provider)
    classify(source)
    first = provider.calls[0][1]
    assert first["target_ids"] == list(range(64))
    assert [unit["id"] for unit in first["context"]][-1] == 75
    second = provider.calls[1][1]
    assert [unit["id"] for unit in second["context"]][0] == 52


def test_all_commercial_cuts_are_unapproved_even_when_review_disabled(monkeypatch):
    provider = Provider("commercial")
    monkeypatch.setattr(processing, "_classifier_request", provider)
    cuts = classify(transcript("A real sales setup.", "Use the discount offer."), review_only=False, threshold=0)
    assert len(cuts) == 1
    assert cuts[0]["start"] == 0 and cuts[0]["end"] == 4
    assert cuts[0]["approved"] is False and cuts[0]["requires_review"] is True
    assert cuts[0]["policy_version"] == scene.POLICY_VERSION
    assert cuts[0]["inference_profile"] == "scene-compact-intent-boundary-reasoning256-2048"
    assert all(row["boundary_audited"] for row in cuts[0]["verification"])
    intent, boundary = (call[2] for call in provider.calls)
    assert intent["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_budget_tokens" not in intent
    assert boundary["chat_template_kwargs"] == {"enable_thinking": True}
    assert boundary["reasoning_budget_tokens"] == 256
    assert boundary["reasoning_format"] == "deepseek"
    assert intent["max_tokens"] == boundary["max_tokens"] == 2048
    assert "ENTIRE established promotional" in boundary["messages"][0]["content"]
    assert "ENTIRE established promotional" not in intent["messages"][0]["content"]


def test_real_free_podcast_preview_and_humorous_setup_are_selected_in_both_stages(monkeypatch):
    source = transcript('The sponsor has a paid service.',
                        'Follow our other podcast for free stories.',
                        'My wallet is auditioning for the premium podcast.',
                        'Buy that paid podcast subscription today.',
                        'Follow this show for the rest of our discussion.')
    provider = Provider(lambda _, identifier: 'editorial' if identifier == 4 else 'commercial')
    monkeypatch.setattr(processing, '_classifier_request', provider)
    audits = []
    cuts = classify(source, decision_sink=audits.append)
    assert [(cut['start'], cut['end']) for cut in cuts] == [(0, 8)]
    assert all(not cut['approved'] for cut in cuts)
    assert audits[0][1]['intent']['label'] == audits[0][1]['boundary']['label'] == 'commercial'
    assert audits[0][4]['intent']['label'] == audits[0][4]['boundary']['label'] == 'editorial'
    assert audits[0][2]['intent']['label'] == audits[0][2]['boundary']['label'] == 'commercial'
    assert all(INSTRUCTIONS in payload['messages'][0]['content'] for _, _, payload, *_ in provider.calls)


def test_indivisible_crosspromo_and_editorial_speech_stays_mixed_review(monkeypatch):
    monkeypatch.setattr(processing, '_classifier_request', Provider('mixed'))
    cuts = classify(transcript('Listen to our other free podcast and now back to our history discussion'), review_only=False)
    assert [(cut['start'], cut['end']) for cut in cuts] == [(0, 2)]
    assert cuts[0]['label'] == 'mixed' and cuts[0]['requires_review'] and not cuts[0]['approved']


def test_editorial_boundary_veto_preserves_navigation_and_full_disagreement_audit(monkeypatch):
    provider = Provider(lambda stage, identifier: "editorial" if stage == "boundaries" and identifier == 0 else "commercial")
    monkeypatch.setattr(processing, "_classifier_request", provider)
    audits = []
    cuts = classify(transcript("We will return to your messages after the break.", "Buy the sponsor's product."),
                    decision_sink=audits.append)
    assert len(cuts) == 1 and cuts[0]["start"] == 2 and cuts[0]["end"] == 4
    assert cuts[0]["label"] == "commercial"
    assert not cuts[0]["approved"] and cuts[0]["requires_review"]
    assert audits[0][0]["intent"]["label"] == "commercial"
    assert audits[0][0]["boundary"]["label"] == "editorial"
    assert audits[0][0]["boundary_audited"] is True


def test_parody_is_not_keyword_gated_and_original_word_timing_is_preserved(monkeypatch):
    original = transcript("Use code RUTABAGA for imaginary vegetables. This is an unpaid sketch.")
    original["segments"][0]["words"] = [
        {"word": "Use", "start": 0, "end": .3, "probability": .99},
        {"word": "code", "start": .3, "end": .5, "probability": .99},
        {"word": "RUTABAGA.", "start": .5, "end": .9, "probability": .99},
        {"word": "Unpaid", "start": 1, "end": 1.4, "probability": .99},
        {"word": "sketch.", "start": 1.4, "end": 2, "probability": .99}]
    source = processing._classification_transcript(original)
    before = deepcopy(source)
    provider, audits = Provider(), []
    monkeypatch.setattr(processing, "_classifier_request", provider)
    assert classify(source, decision_sink=audits.append) == []
    assert source == before
    assert [word for segment in source["segments"] for word in segment["words"]] == original["segments"][0]["words"]
    assert set(audits[0]) == set(range(len(source["segments"])))


def test_coarse_mixed_parent_does_not_invent_sentence_timestamps(monkeypatch):
    source = transcript("A free show introduction. Buy the paid product.")
    provider = Provider(lambda _, identifier: "commercial" if identifier == 1 else "editorial")
    monkeypatch.setattr(processing, "_classifier_request", provider)
    cuts = classify(source)
    assert len(cuts) == 1
    assert cuts[0]["start"] == 0 and cuts[0]["end"] == 2
    assert cuts[0]["label"] == "mixed"
    assert len(cuts[0]["verification"]) == 2
    assert not cuts[0]["approved"]


def valid_answer(first=0, last=2):
    return {"ranges": [{"first_id": first, "last_id": last, "reason": "An editorial scene.",
                        "evidence_id": first, "label": "editorial", "confidence": .99}]}


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "overlap", "gap", "reverse", "invented",
                                        "bool_id", "bool_evidence", "bad_evidence", "label", "reason",
                                        "confidence", "bool_confidence", "extra_fields"])
def test_invalid_range_partitions_and_fields_are_rejected(corruption):
    units = transcript("One", "Two", "Three")["segments"]
    answer = valid_answer()
    row = answer["ranges"][0]
    if corruption == "missing":
        row["last_id"] = 1
    elif corruption == "duplicate":
        answer["ranges"].append(dict(row))
    elif corruption == "overlap":
        row["last_id"] = 1
        answer["ranges"].append(dict(row, first_id=1, last_id=2))
    elif corruption == "gap":
        row["last_id"] = 0
        answer["ranges"].append(dict(row, first_id=2, last_id=2))
    elif corruption == "reverse":
        row.update(first_id=2, last_id=0)
    elif corruption == "invented":
        row["last_id"] = 3
    elif corruption == "bool_id":
        row["first_id"] = False
    elif corruption == "bool_evidence":
        row["evidence_id"] = False
    elif corruption == "bad_evidence":
        row["evidence_id"] = 30
    elif corruption == "label":
        row["label"] = "advert"
    elif corruption == "reason":
        row["reason"] = "x" * 121
    elif corruption == "confidence":
        row["confidence"] = float("nan")
    elif corruption == "bool_confidence":
        row["confidence"] = True
    else:
        row["evidence"] = "Invented text"
    with pytest.raises(ValueError):
        scene._parse(answer, units, units)


def test_nonadjacent_targets_cannot_be_hidden_inside_a_range():
    units = transcript("One", "Two", "Three")["segments"]
    with pytest.raises(ValueError):
        scene._parse(valid_answer(), [units[0], units[2]], units)


def test_mixed_requires_one_self_evidenced_unit_and_always_needs_review(monkeypatch):
    units = transcript("Editorial navigation", "A unit containing both roles", "A real advertisement")["segments"]
    grouped = valid_answer()
    grouped["ranges"][0]["label"] = "mixed"
    with pytest.raises(ValueError, match="Mixed must identify one"):
        scene._parse(grouped, units, units)
    singleton = valid_answer(1, 1)
    singleton["ranges"][0]["label"] = "mixed"
    assert scene._parse(singleton, [units[1]], units)[1]["label"] == "mixed"
    singleton["ranges"][0]["evidence_id"] = 2
    with pytest.raises(ValueError, match="Mixed must identify one"):
        scene._parse(singleton, [units[1]], units)
    provider = Provider("mixed")
    monkeypatch.setattr(processing, "_classifier_request", provider)
    cuts = classify(transcript("One indivisible mixed unit"), review_only=False)
    assert cuts[0]["label"] == "mixed"
    assert cuts[0]["requires_review"] and not cuts[0]["approved"]


def test_exact_evidence_text_is_locally_attached_to_every_expanded_unit():
    units = transcript("One", "Precise evidence:\nCafé offer terms.", "Three")["segments"]
    answer = valid_answer()
    answer["ranges"][0]["evidence_id"] = 1
    before = deepcopy(answer)
    decisions = scene._parse(answer, units, units)
    assert set(decisions) == {0, 1, 2}
    assert all(row["evidence"] == units[1]["text"] for row in decisions.values())
    assert all(row["source_range"] == {"first_id": 0, "last_id": 2} for row in decisions.values())
    assert answer == before


def test_truncated_response_is_rejected_and_closed(monkeypatch):
    provider = Provider(mutate=lambda stage, context, answer, body: {
        "choices": [{"finish_reason": "length", "message": {"content": json.dumps(answer)}}]})
    monkeypatch.setattr(processing, "_classifier_request", provider)
    source = transcript("Single target")
    with pytest.raises(scene._InvalidRanges):
        scene.classify_range_pass(source["segments"], source["segments"], base_url="http://127.0.0.1:8081/v1", model="fixture")
    assert provider.responses[0].closed


def test_invalid_complete_response_splits_without_treating_omissions_as_editorial(monkeypatch):
    def incomplete(stage, context, answer, body):
        if len(context["target_ids"]) > 2:
            answer["ranges"] = []
    provider, audits = Provider(mutate=incomplete), []
    monkeypatch.setattr(processing, "_classifier_request", provider)
    assert classify(transcript(*[f"Sentence {index}." for index in range(8)]), decision_sink=audits.append) == []
    assert len(provider.calls) == 7
    assert len(audits[0]) == 8
    assert all(row["intent"]["label"] == "editorial" for row in audits[0].values())


def test_unresolved_invalid_ranges_fail_without_publishing_a_partial_audit(monkeypatch):
    provider = Provider(mutate=lambda stage, context, answer, body: {"choices": []})
    monkeypatch.setattr(processing, "_classifier_request", provider)
    audits = []
    with pytest.raises(scene._InvalidRanges):
        classify(transcript("One", "Two"), decision_sink=audits.append)
    assert len(provider.calls) == 2
    assert audits == []


def test_transport_failure_does_not_subdivide_or_fallback(monkeypatch):
    calls, audits = [], []
    def fail(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout("Provider timeout")
    monkeypatch.setattr(processing, "_classifier_request", fail)
    with pytest.raises(processing.ProcessingError, match="request failed"):
        classify(transcript("One", "Two"), decision_sink=audits.append)
    assert calls == [1] and audits == []


def test_cancellation_after_first_sweep_stops_boundary_request_and_audit(monkeypatch):
    provider, audits = Provider("commercial"), []
    monkeypatch.setattr(processing, "_classifier_request", provider)
    def cancelled():
        return bool(provider.calls)
    with pytest.raises(processing.ProcessingCancelled):
        classify(transcript("A paid read"), should_cancel=cancelled, decision_sink=audits.append)
    assert len(provider.calls) == 1 and provider.responses[0].closed
    assert audits == []


def test_redirect_and_environment_transport_choices_are_forwarded(monkeypatch):
    provider = Provider()
    monkeypatch.setattr(processing, "_classifier_request", provider)
    classify(transcript("A statement"), allow_redirects=False, trust_env=False, key="fixture-key")
    assert provider.calls[0][4] == {"allow_redirects": False, "trust_env": False}
    assert provider.calls[0][3]["Authorization"] == "Bearer fixture-key"


def test_late_oversized_target_is_rejected_before_any_request(monkeypatch):
    provider = Provider()
    monkeypatch.setattr(processing, "_classifier_request", provider)
    with pytest.raises(ValueError, match="exceeds"):
        classify(transcript(*(["Small"] * 70 + ["x" * 9001])))
    assert provider.calls == []
