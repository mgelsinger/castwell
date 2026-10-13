"""Composition and approval contracts without any model or network inference."""

from copy import deepcopy
import json

import pytest
import requests

from castwell import processing, scene_edges, scene_pipeline as pipeline, scene_review
from castwell.ad_review import _review_units
from castwell.ad_scope import INSTRUCTIONS


def fixture(count=6, *, aligned=True):
    source = {"duration": count * 2, "language": "en", "segments": []}
    for identifier in range(count):
        unit = {"id": identifier, "text": f"Unit {identifier}.", "start": identifier * 2, "end": identifier * 2 + 2}
        if aligned:
            unit["words"] = [{"word": unit["text"], "start": unit["start"], "end": unit["end"], "probability": .99}]
        source["segments"].append(unit)
    units, _ = _review_units(source["segments"])
    audit = {}
    for unit in units:
        identifier = unit["id"]
        judgment = {"id": identifier, "label": "commercial", "confidence": .99, "reason": "Established promotion.",
                    "evidence_id": identifier, "evidence": unit["text"], "source_range": {"first_id": 0, "last_id": len(units) - 1}}
        audit[identifier] = {"segment_id": identifier, "parent_segment_id": unit["parent_segment_id"],
                             "intent": deepcopy(judgment), "boundary": deepcopy(judgment), "boundary_audited": True}
    return source, audit


def proposals(source, audit):
    selected = [row for row in audit.values() if row["boundary"] is not None and row["boundary"]["label"] != "editorial"]
    if not selected:
        return []
    label = "commercial" if all(row["intent"]["label"] == row["boundary"]["label"] == "commercial" for row in selected) else "uncertain"
    return [{"start": source["segments"][0]["start"], "end": source["segments"][-1]["end"], "label": label,
             "approved": False, "requires_review": True, "confidence": .99, "source": "ai",
             "policy_version": scene_review.POLICY_VERSION, "verification": deepcopy(selected)}]


class Response:
    def __init__(self, rows):
        self.rows = rows

    def json(self):
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"decisions": self.rows})}}]}

    def close(self):
        pass


class RangeResponse(Response):
    def json(self):
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'ranges': self.rows})}}]}


def install(monkeypatch, source, audit, *, labels=None, mutate_scene=None, mutate_edges=None):
    calls = {"scene": [], "edges": []}
    def first_stage(transcript, **options):
        calls["scene"].append(options)
        captured = deepcopy(audit)
        if mutate_scene:
            mutate_scene(captured)
        options["decision_sink"](captured)
        return proposals(source, audit)
    def provider(url, headers, payload, *args, **options):
        request = json.loads(payload["messages"][1]["content"])
        calls["edges"].append((request, options))
        rows = []
        for identifier in request["target_ids"]:
            label, role = labels(identifier) if labels else ("commercial", "commercial_speech")
            rows.append({"id": identifier, "label": label, "role": role, "evidence_id": identifier,
                         "reason": "The whole unit has this role."})
        return Response(rows)
    monkeypatch.setattr(scene_review, "classify_scenes", first_stage)
    monkeypatch.setattr(processing, "_classifier_request", provider)
    if mutate_edges:
        original = scene_edges.refine_scene_edges
        def second_stage(*args, **options):
            sink = options["decision_sink"]
            def receive(audit):
                mutate_edges(audit)
                sink(audit)
            options["decision_sink"] = receive
            return original(*args, **options)
        monkeypatch.setattr(scene_edges, "refine_scene_edges", second_stage)
    return calls


def classify(source, **options):
    return pipeline.classify_scene_pipeline(source, base_url="http://127.0.0.1:8081/v1", model="fixture", **options)


@pytest.mark.parametrize("options", [
    {"base_url": "https://api.example.com/v1"}, {"key": "paid-key"},
    {"allow_redirects": True}, {"trust_env": True},
])
def test_remote_key_and_proxy_rejected_before_first_stage(monkeypatch, options):
    source, audit = fixture()
    calls = install(monkeypatch, source, audit)
    settings = {"base_url": "http://127.0.0.1:8081/v1", "model": "fixture", **options}
    with pytest.raises(ValueError):
        pipeline.classify_scene_pipeline(source, **settings)
    assert calls == {"scene": [], "edges": []}


def test_manual_composition_returns_actual_refinement_and_complete_isolated_audits(monkeypatch):
    source, audit = fixture()
    original = deepcopy((source, audit))
    def labels(identifier):
        return ("editorial", "publisher_identity") if identifier == 0 else ("commercial", "persuasive_setup")
    calls = install(monkeypatch, source, audit, labels=labels)
    stages, edges, approvals = [], [], []
    cuts = classify(source, ai_reasoning=True, decision_sink=stages.append,
                    edge_decision_sink=edges.append, approval_sink=approvals.append)
    assert (source, audit) == original
    assert [(cut["start"], cut["end"]) for cut in cuts] == [(2, 12)]
    assert all(not cut["approved"] and not cut["requires_review"] for cut in cuts)
    assert cuts[0]["approval_metadata"]["eligible"]
    assert stages == [audit] and set(edges[0]) == {0, 1, 4, 5}
    assert approvals[0]["scene_audit"] == audit and approvals[0]["endpoint_audit"] == edges[0]
    assert calls["scene"][0]["review_only"] is True and calls["scene"][0]["ai_reasoning"] is False
    assert calls["scene"][0]["allow_redirects"] is False and calls["scene"][0]["trust_env"] is False
    assert calls["edges"][0][1] == {"allow_redirects": False, "trust_env": False}
    stages[0][1]["intent"]["label"] = "mixed"
    assert approvals[0]["scene_audit"][1]["intent"]["label"] == "commercial"
    assert cuts[0]["verification"][0]["intent"]["label"] == "commercial"


def test_actual_composed_stages_preserve_free_crosspromo_between_paid_reads(monkeypatch):
    source, _ = fixture(count=4)
    texts = ['Buy a sponsor plan.', 'Follow our other free podcast.',
             'This joke leads into its paid premium feed.', 'Buy that paid subscription.']
    for unit, text in zip(source['segments'], texts):
        unit['text'] = unit['words'][0]['word'] = text
    prompts, summaries = [], []
    def provider(url, headers, payload, *args, **options):
        prompts.append(payload['messages'][0]['content'])
        request = json.loads(payload['messages'][1]['content'])
        assert options == {'allow_redirects': False, 'trust_env': False}
        if 'scenes' in request:
            rows = [{'id': identifier, 'label': 'commercial', 'role': 'commercial_speech',
                     'evidence_id': identifier, 'reason': 'Actual paid offer or its setup.'}
                    for identifier in request['target_ids']]
            return Response(rows)
        ranges = [{'first_id': identifier, 'last_id': identifier,
                   'label': 'editorial' if identifier == 1 else 'commercial', 'confidence': .99,
                   'evidence_id': identifier, 'reason': 'Free preview.' if identifier == 1 else 'Actual paid offer.'}
                  for identifier in request['target_ids']]
        return RangeResponse(ranges)
    monkeypatch.setattr(processing, '_classifier_request', provider)
    cuts = classify(source, approval_sink=summaries.append)
    assert [(cut['start'], cut['end']) for cut in cuts] == [(0, 2), (4, 8)]
    assert all(not cut['approved'] for cut in cuts)
    assert summaries[0]['policy_version'] == pipeline.POLICY_VERSION
    assert summaries[0]['scene_audit'][1]['intent']['label'] == 'editorial'
    assert 1 not in summaries[0]['edge_plan']['selected_unit_ids']
    assert len(prompts) == 3 and all(INSTRUCTIONS in prompt for prompt in prompts)


def test_indivisible_crosspromo_and_sponsor_blocks_composed_auto_approval(monkeypatch):
    source, audit = fixture(count=1)
    text = 'Follow our free podcast and buy the sponsor plan'
    source['segments'][0]['text'] = source['segments'][0]['words'][0]['word'] = text
    for stage in ('intent', 'boundary'):
        audit[0][stage].update(label='mixed', evidence=text)
    install(monkeypatch, source, audit, labels=lambda _: ('mixed', 'unresolved'))
    summaries = []
    cuts = classify(source, review_only=False, approval_sink=summaries.append)
    assert [(cut['start'], cut['end']) for cut in cuts] == [(0, 2)]
    assert not cuts[0]['approved'] and cuts[0]['requires_review']
    assert summaries[0]['requires_review'] and summaries[0]['unresolved_unit_ids'] == [0]


def test_automatic_approval_requires_verified_retained_endpoints(monkeypatch):
    source, audit = fixture()
    install(monkeypatch, source, audit)
    cuts = classify(source, review_only=False)
    assert cuts[0]["approved"] and not cuts[0]["requires_review"]
    proof = cuts[0]["approval_metadata"]
    assert proof["eligible"] and proof["endpoint_ids"] == [0, 5] and proof["minimum_scored_confidence"] == .99
    assert all(row["commercial_stages"] == ["intent", "boundary"] for row in proof["commercial_agreements"])


def test_endpoint_can_resolve_only_an_actual_retained_scene_start_disagreement(monkeypatch):
    source, audit = fixture()
    audit[0]["intent"]["label"] = "editorial"
    install(monkeypatch, source, audit)
    summaries = []
    cuts = classify(source, review_only=False, approval_sink=summaries.append)
    assert cuts[0]["approved"] and cuts[0]["label"] == "commercial"
    agreement = cuts[0]["approval_metadata"]["commercial_agreements"][0]
    assert agreement == {"segment_id": 0, "commercial_stages": ["boundary", "endpoint"],
                         "endpoint_confidence": None, "endpoint_role": "commercial_speech"}
    assert summaries[0]["unresolved_unit_ids"] == []
    assert summaries[0]["scene_audit"][0]["intent"]["label"] == "editorial"


def test_interior_disagreement_cannot_be_recovered_by_neighbor_endpoint_evidence(monkeypatch):
    source, audit = fixture()
    audit[2]["intent"]["label"] = "editorial"
    install(monkeypatch, source, audit)
    cuts = classify(source, review_only=False)
    assert not cuts[0]["approved"] and cuts[0]["requires_review"]
    assert "intent_disagreement_or_below_threshold" in cuts[0]["approval_metadata"]["reasons"]
    assert cuts[0]["approval_metadata"]["unresolved_source_unit_ids"] == [2]


@pytest.mark.parametrize("defect", ["score", "recovered", "weak_words", "unaligned"])
def test_source_or_score_defects_prevent_automatic_approval(monkeypatch, defect):
    source, audit = fixture()
    if defect == "score":
        audit[2]["boundary"]["confidence"] = .8
    elif defect == "recovered":
        source["segments"][2]["asr_recovered"] = True
    elif defect == "weak_words":
        source["segments"][2]["words"][0]["probability"] = .2
    else:
        source["segments"][2]["words"][0]["start"] += .1
    install(monkeypatch, source, audit)
    cuts = classify(source, review_only=False)
    assert not cuts[0]["approved"] and cuts[0]["requires_review"]


@pytest.mark.parametrize("label", ["mixed", "uncertain"])
def test_unresolved_endpoint_never_approves_scene(monkeypatch, label):
    source, audit = fixture()
    install(monkeypatch, source, audit, labels=lambda i: (label, "unresolved") if i == 0 else ("commercial", "commercial_speech"))
    cuts = classify(source, review_only=False)
    assert (cuts[0]["start"], cuts[0]["end"]) == (0, 12)
    assert not cuts[0]["approved"] and cuts[0]["requires_review"]


def test_trimming_both_audited_starts_cannot_approve_unaudited_new_endpoint(monkeypatch):
    source, audit = fixture()
    install(monkeypatch, source, audit,
            labels=lambda i: ("editorial", "program_navigation") if i < 2 else ("commercial", "commercial_speech"))
    cuts = classify(source, review_only=False)
    assert cuts[0]["start"] == 4 and not cuts[0]["approved"]
    assert "retained_scene_endpoint_not_commercial" in cuts[0]["approval_metadata"]["reasons"]


def test_coarse_mixed_parent_preserves_full_span_and_remains_review(monkeypatch):
    source = {"duration": 4, "language": "en", "segments": [{"id": 10, "start": 0, "end": 4, "text": "A setup. An offer."}]}
    units, _ = _review_units(source["segments"])
    audit = {}
    for unit in units:
        identifier = unit["id"]
        judgment = {"id": identifier, "label": "commercial", "confidence": .99, "reason": "Established promotion.",
                    "evidence_id": identifier, "evidence": unit["text"], "source_range": {"first_id": 0, "last_id": 1}}
        audit[identifier] = {"segment_id": identifier, "parent_segment_id": 10, "intent": deepcopy(judgment),
                             "boundary": deepcopy(judgment), "boundary_audited": True}
    install(monkeypatch, source, audit)
    cuts = classify(source, review_only=False)
    assert [(cut["start"], cut["end"]) for cut in cuts] == [(0, 4)]
    assert not cuts[0]["approved"] and "unaligned_or_coarse_source" in cuts[0]["approval_metadata"]["reasons"]


@pytest.mark.parametrize("stage", ["scene", "edges"])
def test_partial_audit_is_error_and_never_published_as_success(monkeypatch, stage):
    source, audit = fixture()
    options = {"mutate_scene": lambda audit: audit.pop(2)} if stage == "scene" else {"mutate_edges": lambda audit: audit.pop(0)}
    install(monkeypatch, source, audit, **options)
    outputs = []
    with pytest.raises(processing.ProcessingError, match="complete"):
        classify(source, decision_sink=outputs.append, edge_decision_sink=outputs.append, approval_sink=outputs.append)
    assert outputs == []


def test_unresolved_veto_cannot_be_reported_as_no_ads(monkeypatch):
    source, audit = fixture(count=1)
    audit[0]["boundary"]["label"] = "editorial"
    audit[0]["boundary"]["confidence"] = .8
    install(monkeypatch, source, audit)
    outputs = []
    with pytest.raises(processing.ProcessingError, match="empty result"):
        classify(source, approval_sink=outputs.append)
    assert outputs == []


def test_confident_editorial_boundary_veto_cannot_erase_commercial_intent(monkeypatch):
    source, audit = fixture(count=1)
    audit[0]["boundary"]["label"] = "editorial"
    install(monkeypatch, source, audit)
    outputs = []
    with pytest.raises(processing.ProcessingError, match="empty result"):
        classify(source, review_only=False, approval_sink=outputs.append)
    assert outputs == []


def test_global_interior_disagreement_blocks_otherwise_eligible_cuts(monkeypatch):
    source, audit = fixture()
    audit[2]["intent"]["label"] = "editorial"
    install(monkeypatch, source, audit)
    def first_stage(transcript, **options):
        options["decision_sink"](deepcopy(audit))
        return [{"start": unit["start"], "end": unit["end"], "source": "ai", "label": "commercial",
                 "approved": False, "requires_review": True, "confidence": .99,
                 "verification": [deepcopy(audit[unit["id"]])]} for unit in source["segments"]]
    monkeypatch.setattr(scene_review, "classify_scenes", first_stage)
    summaries = []
    cuts = classify(source, review_only=False, approval_sink=summaries.append)
    assert all(not cut["approved"] and cut["requires_review"] for cut in cuts)
    assert cuts[0]["approval_metadata"]["eligible"]  # Its own evidence is complete.
    assert not cuts[2]["approval_metadata"]["eligible"]
    assert cuts[0]["approval_metadata"]["global_requires_review"]
    assert cuts[0]["approval_metadata"]["global_unresolved_unit_ids"] == [2]
    assert "unresolved_source_judgments" in cuts[0]["approval_metadata"]["reasons"]
    assert summaries[0]["requires_review"]


def test_all_editorial_transcript_can_finish_without_fabricated_second_pass(monkeypatch):
    source, audit = fixture(count=1)
    audit[0]["intent"]["label"] = "editorial"
    audit[0].update(boundary=None, boundary_audited=False)
    calls = install(monkeypatch, source, audit)
    summaries = []
    assert classify(source, review_only=False, approval_sink=summaries.append) == []
    assert calls["edges"] == [] and summaries[0]["endpoint_audit"] == {}
    assert summaries[0]["scene_audit"][0]["boundary"] is None


def test_endpoint_transport_failure_propagates_without_callbacks(monkeypatch):
    source, audit = fixture()
    install(monkeypatch, source, audit)
    def fail(*args, **kwargs):
        raise requests.ConnectionError("unavailable local model")
    monkeypatch.setattr(processing, "_classifier_request", fail)
    outputs = []
    with pytest.raises(processing.ProcessingError, match="request failed"):
        classify(source, decision_sink=outputs.append, approval_sink=outputs.append)
    assert outputs == []


def test_cancellation_between_stages_prevents_endpoint_request_and_publishing(monkeypatch):
    source, audit = fixture()
    calls = install(monkeypatch, source, audit)
    outputs = []
    with pytest.raises(processing.ProcessingCancelled):
        classify(source, should_cancel=lambda: bool(calls["scene"]), approval_sink=outputs.append)
    assert calls["edges"] == [] and outputs == []


def test_refinement_cannot_silently_drop_an_unresolved_interior(monkeypatch):
    source, audit = fixture()
    install(monkeypatch, source, audit)
    original = scene_edges.refine_scene_edges
    def drop(*args, **options):
        original(*args, **options)
        return []
    monkeypatch.setattr(scene_edges, "refine_scene_edges", drop)
    with pytest.raises(processing.ProcessingError, match="interior"):
        classify(source, review_only=False)
