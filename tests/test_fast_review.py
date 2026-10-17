"""Behavioral contracts for compact local review without model inference."""

from copy import deepcopy
import json

import pytest

from castwell import fast_review as fast
from castwell.ad_scope import INSTRUCTIONS
from castwell.processing import ProcessingError


@pytest.fixture(autouse=True)
def native_preflight(monkeypatch):
    monkeypatch.setattr(fast, "_native_json", lambda base, path, payload=None:
        {"default_generation_settings": {"n_ctx": 32768}} if path == "/props" else {"input_tokens": 100})


def units(count=600):
    return [
        {"id": index, "start": index * 3.0, "end": (index + 1) * 3.0,
         "text": f"This is source sentence {index} with its original punctuation.",
         "words": [{"word": f" Sentence{index}.", "start": index * 3.0,
                    "end": (index + 1) * 3.0}]}
        for index in range(count)
    ]


def test_partition_owns_every_supplied_id_with_implicit_starts():
    answer = {"ranges": [{"end_id": 8, "label": "editorial"},
                         {"end_id": 10, "label": "commercial"},
                         {"end_id": 11, "label": "uncertain"}]}
    original = deepcopy(answer)
    assert fast.parse_partition(answer, list(range(7, 12))) == {
        7: "editorial", 8: "editorial", 9: "commercial", 10: "commercial",
        11: "uncertain",
    }
    assert answer == original


@pytest.mark.parametrize("answer", [
    {"ranges": []},
    {"ranges": [{"end_id": 8, "label": "editorial"}]},
    {"ranges": [{"end_id": 10, "label": "commercial"}]},
    {"ranges": [{"end_id": True, "label": "commercial"}]},
    {"ranges": [{"end_id": 9.0, "label": "commercial"}]},
    {"ranges": [{"end_id": 9, "label": "keep"}]},
    {"ranges": [{"end_id": 9, "label": "editorial", "confidence": .99}]},
    {"ranges": [{"end_id": 8, "label": "editorial"},
                {"end_id": 8, "label": "commercial"},
                {"end_id": 9, "label": "commercial"}]},
    {"ranges": [{"end_id": 9, "label": "editorial"}], "complete": True},
    {"ranges": [{"end_id": 9, "label": "mixed"}]},
])
def test_partition_rejects_incomplete_invented_or_ambiguous_answers(answer):
    with pytest.raises(ValueError):
        fast.parse_partition(answer, [7, 8, 9])


@pytest.mark.parametrize("identifiers", [[], [7, 7], [7, 9], [9, 8], [True, 2]])
def test_partition_rejects_invalid_source_ownership(identifiers):
    with pytest.raises(ValueError):
        fast.parse_partition({"ranges": [{"end_id": 9, "label": "editorial"}]},
                             identifiers)


def test_sweep_covers_full_source_once_and_keeps_seam_context():
    source = units()
    original = deepcopy(source)
    windows = fast.build_sweep(source, fits=lambda block: len(block) < 450)
    assert len(windows) > 1
    assert [identifier for window in windows for identifier in window["core_ids"]] == list(range(600))
    assert source == original
    for window in windows:
        context = window["units"]
        identifiers = [unit["id"] for unit in context]
        assert identifiers == list(range(identifiers[0], identifiers[-1] + 1))
        assert set(window["core_ids"]).issubset(identifiers)
        assert all(unit == original[unit["id"]] for unit in context)
    for before, after in zip(windows, windows[1:]):
        assert set(unit["id"] for unit in before["units"]) & set(after["core_ids"])
        assert set(unit["id"] for unit in after["units"]) & set(before["core_ids"])


def transcript(tokens, *, word_duration=.5):
    words = [{"word": token if index == 0 else " " + token,
              "start": index * word_duration, "end": (index + 1) * word_duration,
              "probability": .98}
             for index, token in enumerate(tokens)]
    return {"language": "en", "duration": len(words) * word_duration,
            "segments": [{"id": 0, "start": 0, "end": len(words) * word_duration,
                          "text": " ".join(tokens), "words": words}]}


class Response:
    def __init__(self, body):
        self.body, self.closed = body, False

    def json(self):
        return self.body

    def close(self):
        self.closed = True


class Provider:
    def __init__(self, labels):
        self.labels, self.calls, self.responses = labels, [], []

    def __call__(self, url, headers, payload, *args, **kwargs):
        stage = "words" if "Each numbered token" in payload["messages"][0]["content"] else "scenes"
        identifiers = payload["response_format"]["json_schema"]["schema"]["properties"]["ranges"]["items"]["properties"]["end_id"]["enum"]
        self.calls.append((stage, identifiers, url, headers, payload, kwargs))
        ranges = []
        for identifier in identifiers:
            label = self.labels(stage, identifier, len(self.calls))
            if ranges and ranges[-1]["label"] == label and label != "mixed":
                ranges[-1]["end_id"] = identifier
            else:
                ranges.append({"end_id": identifier, "label": label})
        response = Response({"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"ranges": ranges})}}]})
        self.responses.append(response)
        return response


def classify(source, **options):
    return fast.classify_fast(source, base_url="http://127.0.0.1:8081/v1",
                              model="fixture", **options)


def test_word_review_selects_real_promo_but_preserves_editorial_inside_original_parent(monkeypatch):
    source = transcript("Welcome back. Imagine a wallet with legs. Buy the plan. "
                        "Follow Freecast for free. Terms apply. Back to today's story.".split())
    original, audit = deepcopy(source), []
    commercial = set(range(2, 16))
    provider = Provider(lambda stage, identifier, _: "mixed" if stage == "scenes" else
                        "commercial" if identifier in commercial else "editorial")
    monkeypatch.setattr(fast, "_classifier_request", provider)
    cuts = classify(source, review_only=False, threshold=0, decision_sink=audit.append)
    assert source == original
    assert [(cut["start"], cut["end"]) for cut in cuts] == [(1, 8)]
    assert all(cut["approved"] is False and cut["requires_review"] is True for cut in cuts)
    assert set(audit[0]["boundary_audit"]["word_votes"]) == set(range(20))
    assert audit[0]["word_decisions"] == {i: "commercial" if i in commercial else "editorial" for i in range(20)}
    assert audit[0]["source_word_count"] == 20
    assert audit[0]["automatic_acceptance_validated"] is False
    assert all(response.closed for response in provider.responses)
    for stage, _, url, headers, payload, options in provider.calls:
        assert url == "http://127.0.0.1:8081/v1/chat/completions"
        assert headers == {"Content-Type": "application/json"}
        assert options["allow_redirects"] is False and options["trust_env"] is False
        assert payload["chat_template_kwargs"] == {"enable_thinking": True}
        if stage == "words":
            assert payload["reasoning_budget_tokens"] == 1024
            assert "FULL SCENE CONTEXT" in payload["messages"][1]["content"]
        assert payload["max_tokens"] == (2048 if stage == "words" else 8192)
        assert INSTRUCTIONS in payload["messages"][0]["content"]


def test_editorial_episode_receives_complete_sweep_without_word_requests(monkeypatch):
    provider, audit = Provider(lambda *_: "editorial"), []
    monkeypatch.setattr(fast, "_classifier_request", provider)
    assert classify(transcript(["One.", "Two.", "Three."]), decision_sink=audit.append) == []
    assert [call[0] for call in provider.calls] == ["scenes"]
    assert audit[0]["sweep_votes"] == {0: ["editorial"], 1: ["editorial"], 2: ["editorial"]}
    assert audit[0]["boundary_audit"]["word_votes"] == {}


def test_fake_rutabaga_podcast_sketch_and_current_show_follow_cannot_be_reopened_by_word_pass(monkeypatch):
    source = transcript("Imagine Rutabaga Radio. Our fictional podcast sings to turnips. "
                        "That was an unpaid parody. Follow this show for our next episode.".split())
    original, audit = deepcopy(source), []
    provider = Provider(lambda *_: "editorial")
    monkeypatch.setattr(fast, "_classifier_request", provider)
    assert classify(source, decision_sink=audit.append) == []
    assert source == original
    assert [call[0] for call in provider.calls] == ["scenes"]
    assert set(audit[0]["word_decisions"].values()) == {"editorial"}
    assert audit[0]["boundary_audit"]["word_votes"] == {}


def test_context_vote_nominates_scene_even_when_core_owner_calls_it_editorial(monkeypatch):
    def overlapping(source, **options):
        return [{"core_ids": [0], "units": source[:2]},
                {"core_ids": [1, 2], "units": source[1:]}]

    def labels(stage, identifier, request):
        return "commercial" if stage == "words" or identifier == 1 and request == 1 else "editorial"

    provider, audit = Provider(labels), []
    monkeypatch.setattr(fast, "build_sweep", overlapping)
    monkeypatch.setattr(fast, "_classifier_request", provider)
    cuts = classify(transcript(["Opening.", "Offer.", "Return."]), decision_sink=audit.append)
    assert audit[0]["sweep_votes"][1] == ["commercial", "editorial"]
    assert audit[0]["global_decisions"][1] == "uncertain"
    assert set(audit[0]["boundary_audit"]["word_votes"]) == {1}
    assert [(cut["start"], cut["end"]) for cut in cuts] == [(.5, 1)]


def test_boundary_uncertainty_stays_unresolved_and_global_editorial_cannot_be_cut(monkeypatch):
    provider, audit = Provider(lambda stage, identifier, _: "uncertain" if stage == "words" else
                               "commercial" if identifier == 1 else "editorial"), []
    monkeypatch.setattr(fast, "_classifier_request", provider)
    assert classify(transcript(["Opening.", "Offer.", "Return."]), decision_sink=audit.append) == []
    assert audit[0]["boundary_audit"]["word_votes"] == {1: ["uncertain"]}
    assert audit[0]["word_decisions"][1] == "uncertain"
    assert audit[0]["unresolved_word_ids"] == [1]


def test_native_preflight_counts_prompt_schema_and_reserves_all_output(monkeypatch):
    payloads = []

    def native(base, path, payload=None):
        payloads.append((path, payload))
        return {"input_tokens": 10000}

    monkeypatch.setattr(fast, "_native_json", native)
    options = dict(stage="scenes", model="fixture", base_url="http://localhost:8081/v1", should_cancel=None)
    assert fast._fits(units(2), context_tokens=18192, **options)
    assert not fast._fits(units(2), context_tokens=18191, **options)
    assert payloads[0][0] == "/v1/chat/completions/input_tokens"
    assert payloads[0][1] == fast._payload(units(2), stage="scenes", model="fixture")


def test_context_overflow_rejects_indivisible_passage_before_inference(monkeypatch):
    monkeypatch.setattr(fast, "_fits", lambda *a, **k: False)
    monkeypatch.setattr(fast, "_request", lambda *a, **k: pytest.fail("Must finish preflight first"))
    with pytest.raises(ProcessingError, match="no source was truncated"):
        classify(transcript(["One."]))


def test_whole_episode_fits_in_one_complete_request():
    source = units(600)
    windows = fast.build_sweep(source, fits=lambda _: True)
    assert windows == [{"core_ids": list(range(600)), "units": source}]


@pytest.mark.parametrize('text', ['This episode is brought to you by Example. Buy it today.',
                                'Follow our other free podcast. New episodes every Tuesday.'])
def test_clear_sponsor_or_podcast_promo_omission_fails_before_boundary_or_audit_publication(monkeypatch, text):
    provider, audit = Provider(lambda *_: "editorial"), []
    monkeypatch.setattr(fast, "_classifier_request", provider)
    with pytest.raises(ProcessingError, match="Possible missed sponsor reads"):
        classify(transcript(text.split()), decision_sink=audit.append)
    assert audit == []
    assert [call[0] for call in provider.calls] == ["scenes"]


@pytest.mark.parametrize("options", [
    {"base_url": "https://example.com/v1"},
    {"base_url": "http://person:secret@localhost:8081/v1"},
    {"base_url": "http://127.0.0.1:8081/v1?token=secret"},
    {"key": "must-not-be-sent"},
])
def test_remote_or_authenticated_requests_fail_before_contact(monkeypatch, options):
    def forbidden(*args, **kwargs):
        pytest.fail("Preflight must reject this configuration before any request")

    monkeypatch.setattr(fast, "_classifier_request", forbidden)
    kwargs = {"base_url": "http://127.0.0.1:8081/v1", "model": "fixture", **options}
    with pytest.raises(ValueError):
        fast.classify_fast(transcript(["Source."]), **kwargs)


def test_coarse_transcript_is_rejected_before_model_call(monkeypatch):
    source = transcript(["Source."])
    del source["segments"][0]["words"]
    monkeypatch.setattr(fast, "_classifier_request", lambda *a, **k: pytest.fail("Missing word times must fail locally"))
    with pytest.raises(ProcessingError, match="word timestamps"):
        classify(source)


@pytest.mark.parametrize("invalid", ["missing", "unknown_label", "extra"])
def test_second_pass_cannot_silently_turn_missing_or_invalid_words_into_editorial(monkeypatch, invalid):
    def response(source, *, stage, **options):
        answer = {unit["id"]: "commercial" for unit in source}
        if stage == "words":
            if invalid == "missing":
                answer.pop(source[0]["id"])
            elif invalid == "unknown_label":
                answer[source[0]["id"]] = "ignored"
            else:
                answer[source[-1]["id"] + 1] = "editorial"
        return answer

    monkeypatch.setattr(fast, "_request", response)
    with pytest.raises(ProcessingError):
        classify(transcript(["Opening.", "Offer.", "Return."]))
