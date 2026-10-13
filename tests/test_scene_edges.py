"""Endpoint-only contracts using frozen-shaped fixtures and mocked responses."""

from copy import deepcopy
import json

import pytest
import requests

from castwell import processing, scene_edges as edges


def fixture(texts=None, ranges=None, selected=None, aligned=True):
    texts = texts or ["Publisher identity", "A humorous setup", "The persuasive conversation",
                      "The actual offer", "Offer terms", "Return to the program"]
    units = []
    for index, text in enumerate(texts):
        unit = {"id": index, "start": index * 2, "end": (index + 1) * 2, "text": text}
        if aligned:
            unit["words"] = [{"word": text, "start": unit["start"], "end": unit["end"], "probability": .99}]
        units.append(unit)
    source = {"duration": len(units) * 2, "language": "en", "segments": units}
    ranges = ranges or [(0, len(units) - 1)]
    audit = {}
    for index in range(len(units)):
        first, last = next(bounds for bounds in ranges if bounds[0] <= index <= bounds[1])
        decision = {"id": index, "label": "commercial", "confidence": .99, "reason": "Established promotion.",
                    "evidence_id": first, "evidence": texts[first], "source_range": {"first_id": first, "last_id": last}}
        audit[index] = {"segment_id": index, "parent_segment_id": index, "intent": deepcopy(decision),
                        "boundary": deepcopy(decision), "boundary_audited": True}
    selected = set(range(len(units))) if selected is None else selected
    cuts = [{"start": units[index]["start"], "end": units[index]["end"], "source": "ai", "approved": False,
             "requires_review": True, "confidence": .99, "label": "commercial", "policy_version": "scene-boundary-v4",
             "verification": [deepcopy(audit[index])]} for index in sorted(selected)]
    return source, cuts, audit


class Response:
    def __init__(self, answer, finish="stop"):
        self.body = {"choices": [{"finish_reason": finish, "message": {"content": json.dumps(answer)}}]}
        self.closed = False

    def json(self):
        return self.body

    def close(self):
        self.closed = True


class Provider:
    def __init__(self, labels=None, mutate=None):
        self.labels, self.mutate = labels, mutate
        self.calls, self.responses = [], []

    def __call__(self, url, headers, payload, *args, **kwargs):
        request = json.loads(payload["messages"][1]["content"])
        self.calls.append((request, payload, kwargs))
        rows = []
        for identifier in request["target_ids"]:
            label, role, evidence = self.labels(identifier) if self.labels else ("commercial", "commercial_speech", identifier)
            rows.append({"id": identifier, "label": label, "role": role, "evidence_id": evidence,
                         "reason": "This whole unit has that role in the supplied scene."})
        answer = {"decisions": rows}
        if self.mutate:
            self.mutate(answer, request)
        response = Response(answer)
        self.responses.append(response)
        return response


def refine(inputs, **options):
    return edges.refine_scene_edges(*inputs, base_url="http://127.0.0.1:8081/v1", model="fixture", **options)


def test_neutral_publication_bumper_overrides_adjacent_offer_inference_transparently(monkeypatch):
    inputs = fixture(["This is a Sample Network podcast, guaranteed human.", "Try the real offer today."])
    provider, audits = Provider(), []
    monkeypatch.setattr(processing, "_classifier_request", provider)
    result = refine(inputs, decision_sink=audits.append)
    assert [(cut["start"], cut["end"]) for cut in result] == [(2, 4)]
    decision = audits[0][0]
    assert decision["trimmed"] and decision["role"] == "publisher_identity"
    assert decision["structural_rule"] == "neutral-publication-bumper-v1"
    assert decision["model_decision"]["label"] == "commercial"


@pytest.mark.parametrize("text", [
    "This is a Sample Network podcast, guaranteed human. Subscribe to our paid tier.",
    "Buy a Sample Network podcast, guaranteed human.",
    "A Sample Network podcast about guaranteed human savings.",
    "This is a Sample Network podcast. Try our offer now.",
])
def test_publication_rule_cannot_protect_a_pitch_or_partial_identification(text):
    assert not edges._neutral_publication_bumper(text)


def test_publication_rule_never_trims_coarse_unaligned_audio(monkeypatch):
    inputs = fixture(["This is a Sample Network podcast, guaranteed human.", "Try our offer."], aligned=False)
    monkeypatch.setattr(processing, "_classifier_request", Provider())
    audits = []
    result = refine(inputs, decision_sink=audits.append)
    assert [(cut["start"], cut["end"]) for cut in result] == [(0, 2), (2, 4)]
    assert "structural_rule" not in audits[0][0]


def test_plan_requests_only_scene_endpoints_and_retains_whole_scene_context():
    inputs = fixture([f"Unit {index}" for index in range(30)], ranges=[(0, 9), (10, 19), (20, 29)])
    plan = edges.build_edge_plan(*inputs)
    targets = [identifier for request in plan["requests"] for identifier in request["target_ids"]]
    assert targets == [0, 1, 8, 9, 10, 11, 18, 19, 20, 21, 28, 29]
    assert len(plan["requests"]) == 2 and len(targets) == len(set(targets))
    for request in plan["requests"]:
        assert len(request["target_ids"]) <= 8
        context_ids = {unit["id"] for unit in request["context"]}
        for scene in request["scenes"]:
            assert set(range(scene["first_id"], scene["last_id"] + 1)) <= context_ids
    assert plan["review_unit_count"] == 30


def test_trimming_publisher_prefix_and_editorial_suffix_preserves_paid_setup_and_interiors(monkeypatch):
    inputs = fixture()
    original, audits = deepcopy(inputs), []
    def labels(identifier):
        if identifier == 0:
            return "editorial", "publisher_identity", 0
        if identifier == 5:
            return "editorial", "editorial_return", 5
        return "commercial", "persuasive_setup", 3
    provider = Provider(labels)
    monkeypatch.setattr(processing, "_classifier_request", provider)
    cuts = refine(inputs, decision_sink=audits.append)
    assert inputs == original
    assert [(cut["start"], cut["end"]) for cut in cuts] == [(2, 4), (4, 6), (6, 8), (8, 10)]
    assert cuts[1]["verification"] == inputs[1][2]["verification"]
    assert all(not cut["approved"] and cut["requires_review"] for cut in cuts)
    assert audits[0][0]["trimmed"] and audits[0][5]["trimmed"]
    assert audits[0][1]["evidence"] == inputs[0]["segments"][3]["text"]
    request, payload, transport = provider.calls[0]
    assert request["target_ids"] == [0, 1, 4, 5]
    assert payload["max_tokens"] == 1024 and payload["reasoning_budget_tokens"] == 256
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}
    assert transport == {"allow_redirects": False, "trust_env": False}
    assert all(response.closed for response in provider.responses)


def test_mixed_uncertain_and_noncontiguous_editorial_targets_are_retained_for_review(monkeypatch):
    def labels(identifier):
        return {0: ("commercial", "persuasive_setup", 3), 1: ("editorial", "publisher_identity", 1),
                4: ("mixed", "unresolved", 4), 5: ("uncertain", "unresolved", 5)}[identifier]
    inputs, audits = fixture(), []
    monkeypatch.setattr(processing, "_classifier_request", Provider(labels))
    cuts = refine(inputs, decision_sink=audits.append)
    assert len(cuts) == len(inputs[1])
    assert not any(row["trimmed"] for row in audits[0].values())
    assert audits[0][4]["requires_review"] and audits[0][5]["requires_review"]
    assert all(cut["requires_review"] and not cut["approved"] for cut in cuts)


def test_unaligned_parent_has_no_invented_sentence_cut(monkeypatch):
    inputs = fixture(["A publisher identity"], aligned=False)
    audits = []
    monkeypatch.setattr(processing, "_classifier_request", Provider(lambda _: ("editorial", "publisher_identity", 0)))
    cuts = refine(inputs, decision_sink=audits.append)
    assert [(cut["start"], cut["end"]) for cut in cuts] == [(0, 2)]
    assert audits[0][0]["trim_status"] == "unaligned_review" and audits[0][0]["requires_review"]


@pytest.mark.parametrize("corruption", ["omitted", "duplicate", "bool_id", "foreign_editorial", "role", "long_reason", "timestamp"])
def test_complete_endpoint_validation_rejects_corruption(corruption):
    plan = edges.build_edge_plan(*fixture())
    request = plan["requests"][0]
    answer = {"decisions": [{"id": identifier, "label": "editorial", "role": "publisher_identity",
                            "evidence_id": identifier, "reason": "The unit is a credit."} for identifier in request["target_ids"]]}
    if corruption == "omitted":
        answer["decisions"].pop()
    elif corruption == "duplicate":
        answer["decisions"].append(deepcopy(answer["decisions"][0]))
    elif corruption == "bool_id":
        answer["decisions"][0]["id"] = False
    elif corruption == "foreign_editorial":
        answer["decisions"][0]["evidence_id"] = 3
    elif corruption == "role":
        answer["decisions"][0]["role"] = "offer_terms"
    elif corruption == "long_reason":
        answer["decisions"][0]["reason"] = "x" * 81
    else:
        answer["decisions"][0]["start"] = 0
    with pytest.raises(ValueError):
        edges._parse(answer, request)


def test_invalid_output_splits_targets_without_relabeling_scene_interiors(monkeypatch):
    def omit(answer, request):
        if len(request["target_ids"]) > 2:
            answer["decisions"] = []
    provider = Provider(mutate=omit)
    monkeypatch.setattr(processing, "_classifier_request", provider)
    inputs, audits = fixture(), []
    cuts = refine(inputs, decision_sink=audits.append)
    assert len(provider.calls) == 3 and set(audits[0]) == {0, 1, 4, 5}
    assert len(cuts) == 6
    assert all(call[0]["context"] == provider.calls[0][0]["context"] for call in provider.calls)


def test_transport_failure_never_falls_back_or_publishes_partial_audit(monkeypatch):
    calls, audits = [], []
    def fail(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout()
    monkeypatch.setattr(processing, "_classifier_request", fail)
    with pytest.raises(processing.ProcessingError, match="request failed"):
        refine(fixture(), decision_sink=audits.append)
    assert calls == [1] and audits == []


def test_cancellation_closes_response_and_publishes_no_audit(monkeypatch):
    provider, audits = Provider(), []
    monkeypatch.setattr(processing, "_classifier_request", provider)
    with pytest.raises(processing.ProcessingCancelled):
        refine(fixture(), should_cancel=lambda: bool(provider.calls), decision_sink=audits.append)
    assert len(provider.calls) == 1 and provider.responses[0].closed and audits == []


def test_incomplete_input_audit_and_preapproved_cut_are_rejected_before_requests(monkeypatch):
    provider = Provider()
    monkeypatch.setattr(processing, "_classifier_request", provider)
    source, cuts, audit = fixture()
    with pytest.raises(ValueError, match="complete matching"):
        refine((source, cuts, {key: row for key, row in audit.items() if key != 0}))
    cuts[0]["approved"] = True
    with pytest.raises(ValueError, match="unapproved"):
        refine((source, cuts, audit))
    assert provider.calls == []


def test_remote_endpoint_is_refused_before_any_model_request(monkeypatch):
    provider = Provider()
    monkeypatch.setattr(processing, "_classifier_request", provider)
    with pytest.raises(ValueError, match="loopback"):
        edges.refine_scene_edges(*fixture(), base_url="https://api.example.com/v1", model="fixture")
    assert provider.calls == []
