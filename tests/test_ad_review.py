"""Verified classification contract tests using only mocked model responses."""

from copy import deepcopy
import json
from unittest.mock import patch

import pytest
import requests

from castwell import processing


@pytest.fixture(autouse=True)
def fixed_limits(monkeypatch):
    monkeypatch.setenv('CASTWELL_AI_WINDOW_CHARS', '18000')
    monkeypatch.setenv('CASTWELL_AI_CONTEXT_SEGMENTS', '12')
    monkeypatch.setenv('CASTWELL_AI_TIMEOUT', '180')


def transcript(*texts):
    return {'language': 'en', 'duration': len(texts) * 2,
            'segments': [{'id': index, 'start': index * 2, 'end': (index + 1) * 2, 'text': text}
                         for index, text in enumerate(texts)]}


def classify(source, **options):
    config = {'ai_policy': 'verified', 'ai_base_url': 'http://127.0.0.1:8081/v1', 'ai_model': 'fixture',
              'ai_key': '', 'auto_approve_threshold': .90, 'review_only': False}
    config.update(options.pop('config', {}))
    return processing.detect_ads(source, 'ai', config=config, **options)


class Response:
    def __init__(self, body):
        self.body = body
        self.closed = False

    def json(self):
        return self.body

    def close(self):
        self.closed = True


class Provider:
    def __init__(self, labels='editorial', confidence=.99, mutate=None):
        self.labels = labels
        self.confidence = confidence
        self.mutate = mutate
        self.calls = []
        self.responses = []

    def __call__(self, url, headers, payload, *args, **kwargs):
        context = json.loads(payload['messages'][1]['content'])
        stage = 'intent' if 'commercial INTENT' in payload['messages'][0]['content'] else 'boundaries'
        self.calls.append((stage, context, payload, headers, kwargs))
        decisions = []
        for identifier in context['target_ids']:
            label = self.labels(stage, identifier) if callable(self.labels) else self.labels
            decisions.append({'id': identifier, 'label': label, 'confidence': self.confidence,
                              'reason': 'The full scene establishes this role.',
                              'evidence_id': identifier})
        answer = {'decisions': decisions}
        body = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}]}
        if self.mutate:
            replacement = self.mutate(stage, answer, body)
            if replacement is not None:
                body = replacement
            else:
                body['choices'][0]['message']['content'] = json.dumps(answer)
        response = Response(body)
        self.responses.append(response)
        return response


def test_every_unit_is_owned_once_in_each_of_two_distinct_passes():
    source = transcript(*[f'Editorial sentence {index}.' for index in range(28)])
    provider = Provider()
    with patch('castwell.processing._classifier_request', side_effect=provider):
        assert classify(source) == []
    assert len(provider.calls) == 4
    for stage in ('intent', 'boundaries'):
        identifiers = [identifier for kind, context, *_ in provider.calls if kind == stage for identifier in context['target_ids']]
        assert identifiers == list(range(28))
    assert provider.calls[0][2]['messages'][0]['content'] != provider.calls[1][2]['messages'][0]['content']
    assert provider.calls[0][2]['response_format']['json_schema']['strict'] is True
    assert all(response.closed for response in provider.responses)


def test_editorial_agreement_suppresses_parody_keyword_false_positive():
    source = transcript('The following is an unpaid fictional sponsor comedy sketch.',
                        'This episode is sponsored by Invisible Umbrellas.',
                        'Use promo code RAIN for a free trial.', 'Now back to the show.')
    assert processing.detect_ads(source, 'heuristic', config={'ai_policy': 'legacy'})
    provider = Provider('editorial')
    with patch('castwell.processing._classifier_request', side_effect=provider):
        assert classify(source) == []


def test_two_commercial_judgments_approve_only_after_review_mode_opt_out():
    provider = Provider('commercial', confidence=.96)
    with patch('castwell.processing._classifier_request', side_effect=provider):
        approved = classify(transcript('A real paid promotion.'))
        review = classify(transcript('A real paid promotion.'), config={'review_only': True})
    assert approved[0]['approved'] is True
    assert approved[0]['requires_review'] is False
    assert len(approved[0]['verification']) == 1
    assert review[0]['approved'] is False
    assert review[0]['requires_review'] is False


def test_evidence_source_id_attaches_exact_context_text_without_model_quote():
    evidence = 'Offer TERMS:\n' + ('Choose  Café credit with the weekly plan; ' * 6) + 'Offer ends tonight'
    source = transcript('A short sponsor introduction.', evidence)
    original = deepcopy(source)

    def choose_surrounding_source(stage, answer, body):
        for row in answer['decisions']:
            row['evidence_id'] = 1

    provider = Provider('commercial', mutate=choose_surrounding_source)
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(source)
    assert source == original
    assert cuts[0]['policy_version'] == 'intent-boundary-v8'
    assert len(evidence) > 160
    assert len(cuts[0]['verification']) == 2
    for record in cuts[0]['verification']:
        for stage in ('intent', 'boundary'):
            assert record[stage]['evidence_id'] == 1
            assert record[stage]['evidence'] == evidence
    for _, context, payload, *_ in provider.calls:
        assert context['context'][1]['text'] == evidence
        schema = payload['response_format']['json_schema']['schema']['properties']['decisions']['items']
        assert 'evidence' not in schema['required']
        assert 'evidence' not in schema['properties']
        assert schema['additionalProperties'] is False
        assert 'Choose evidence_id from the supplied context IDs' in payload['messages'][0]['content']
    # Resolving the text creates local audit records, not edits to model output.
    for response in provider.responses:
        rows = json.loads(response.body['choices'][0]['message']['content'])['decisions']
        assert all('evidence' not in row for row in rows)


@pytest.mark.parametrize('intent,boundary', [('commercial', 'editorial'), ('editorial', 'commercial'),
                                          ('commercial', 'mixed'), ('mixed', 'mixed'),
                                          ('uncertain', 'uncertain'), ('commercial', 'uncertain')])
def test_disagreement_mixed_and_uncertain_units_never_auto_approve(intent, boundary):
    provider = Provider(lambda stage, _: intent if stage == 'intent' else boundary)
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(transcript('Commercial and editorial intent need review.'))
    assert len(cuts) == 1
    assert cuts[0]['approved'] is False
    assert cuts[0]['requires_review'] is True


@pytest.mark.parametrize('intent_score,boundary_score', [(.95, .8), (.8, .95), (0, 0)])
@pytest.mark.parametrize('threshold', [.5, .9, 1])
@pytest.mark.parametrize('review_only', [True, False])
def test_editorial_agreement_is_independent_of_approval_threshold(intent_score, boundary_score, threshold, review_only):
    def scores(stage, rows, body):
        for row in rows['decisions']:
            row['confidence'] = intent_score if stage == 'intent' else boundary_score
    provider = Provider('editorial', mutate=scores)
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(transcript('An editorial source credit.'), config={
            'auto_approve_threshold': threshold, 'review_only': review_only})
    assert cuts == []
    assert len(provider.calls) == 2


@pytest.mark.parametrize('score', [0, 1])
@pytest.mark.parametrize('threshold', [.5, 1])
def test_explicit_uncertainty_remains_a_review_suggestion_at_any_score(score, threshold):
    provider = Provider('uncertain', confidence=score)
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(transcript('The commercial relationship is unresolved.'), config={
            'auto_approve_threshold': threshold, 'review_only': False})
    assert len(cuts) == 1 and cuts[0]['label'] == 'uncertain'
    assert cuts[0]['requires_review'] and not cuts[0]['approved']


@pytest.mark.parametrize('threshold,review_only,approved', [(.8, False, True), (.9, False, False), (.8, True, False)])
def test_approval_threshold_still_controls_two_commercial_judgments(threshold, review_only, approved):
    with patch('castwell.processing._classifier_request', side_effect=Provider('commercial', confidence=.8)):
        cuts = classify(transcript('An actual commercial read.'), config={
            'auto_approve_threshold': threshold, 'review_only': review_only})
    assert len(cuts) == 1 and cuts[0]['label'] == 'commercial'
    assert cuts[0]['approved'] is approved
    assert cuts[0]['requires_review'] is (threshold > .8)


def test_approval_threshold_does_not_change_inference_requests():
    low, high = Provider('editorial', confidence=.8), Provider('editorial', confidence=.8)
    source = transcript('An editorial source credit.')
    with patch('castwell.processing._classifier_request', side_effect=low):
        assert classify(source, config={'auto_approve_threshold': .5}) == []
    with patch('castwell.processing._classifier_request', side_effect=high):
        assert classify(source, config={'auto_approve_threshold': 1}) == []
    assert low.calls == high.calls


def test_low_score_editorial_child_keeps_coarse_commercial_parent_mixed():
    provider = Provider(lambda _, identifier: 'commercial' if identifier == 0 else 'editorial', confidence=.8)
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(transcript('An actual commercial read. Editorial discussion resumes.'))
    assert len(cuts) == 1 and (cuts[0]['start'], cuts[0]['end']) == (0, 2)
    assert cuts[0]['label'] == 'mixed' and cuts[0]['requires_review'] and not cuts[0]['approved']
    assert len(cuts[0]['verification']) == 2
    assert cuts[0]['verification'][1]['intent']['label'] == 'editorial'
    assert cuts[0]['verification'][1]['boundary']['label'] == 'editorial'


def test_low_score_editorial_gap_keeps_commercial_reads_separate():
    provider = Provider(lambda _, identifier: 'editorial' if identifier == 1 else 'commercial', confidence=.8)
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(transcript('Commercial one.', 'Editorial discussion.', 'Commercial two.'), config={
            'auto_approve_threshold': .5, 'review_only': False})
    assert [(cut['start'], cut['end']) for cut in cuts] == [(0, 2), (4, 6)]
    assert all(cut['approved'] for cut in cuts)


def aligned_transcript(probability=.99):
    tokens = ['Science', ' begins.', 'Sponsor', ' offer.', 'Science', ' returns.']
    return {'language': 'en', 'duration': 6, 'segments': [{
        'id': 41, 'start': 0, 'end': 6, 'text': 'Science begins. Sponsor offer. Science returns.',
        'words': [{'word': word, 'start': index, 'end': index + 1, 'probability': probability if index == 2 else .99}
                  for index, word in enumerate(tokens)],
    }]}


def test_word_sentence_boundaries_keep_surrounding_editorial_audio():
    source = aligned_transcript()
    unchanged = deepcopy(source)
    provider = Provider(lambda _, identifier: 'commercial' if identifier == 1 else 'editorial')
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(source)
    assert [(cut['start'], cut['end'], cut['approved']) for cut in cuts] == [(2, 4, True)]
    assert [row['text'] for row in provider.calls[0][1]['context']] == ['Science begins.', 'Sponsor offer.', 'Science returns.']
    assert source == unchanged


def test_low_word_probability_blocks_automatic_cut_even_when_passes_agree():
    provider = Provider(lambda _, identifier: 'commercial' if identifier == 1 else 'editorial')
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(aligned_transcript(probability=.2))
    assert cuts[0]['approved'] is False
    assert cuts[0]['requires_review'] is True
    assert 'uncertain words' in cuts[0]['reason']


def test_separate_ads_do_not_merge_across_editorial_units():
    provider = Provider(lambda _, identifier: 'editorial' if identifier == 1 else 'commercial')
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(transcript('First commercial.', 'Editorial interlude.', 'Second commercial.'))
    assert [(cut['start'], cut['end']) for cut in cuts] == [(0, 2), (4, 6)]


def test_commercial_units_do_not_merge_across_unclassified_audio_gaps():
    source = transcript('First commercial.', 'Second commercial.')
    source['segments'][1]['start'] = 2.5
    with patch('castwell.processing._classifier_request', side_effect=Provider('commercial')):
        cuts = classify(source)
    assert [(cut['start'], cut['end']) for cut in cuts] == [(0, 2), (2.5, 4)]


def test_touching_commercial_units_merge_with_both_verification_records():
    with patch('castwell.processing._classifier_request', side_effect=Provider('commercial')):
        cuts = classify(transcript('First pitch sentence.', 'Second pitch sentence.'))
    assert [(cut['start'], cut['end']) for cut in cuts] == [(0, 4)]
    assert [row['segment_id'] for row in cuts[0]['verification']] == [0, 1]


def test_all_windows_are_validated_before_any_model_request():
    source = transcript(*(['Normal text.'] * 24 + ['x' * 4501]))
    with patch('castwell.processing._classifier_request') as request:
        with pytest.raises(ValueError, match='exceeds'):
            classify(source)
    request.assert_not_called()


@pytest.mark.parametrize('mutate', [
    lambda rows: rows.pop(),
    lambda rows: rows.append(deepcopy(rows[0])),
    lambda rows: rows[0].update(id=999),
    lambda rows: rows[0].update(id=True),
    lambda rows: rows[0].update(evidence_id=999),
    lambda rows: rows[0].update(evidence_id=True),
    lambda rows: rows[0].update(evidence_id='0'),
    lambda rows: rows[0].pop('evidence_id'),
    lambda rows: rows[0].update(evidence='Invented words not present in this transcript'),
    lambda rows: rows[0].update(evidence='A real paid promotion.'),
    lambda rows: rows[0].update(reason=' '),
    lambda rows: rows[0].update(reason='x' * 301),
    lambda rows: rows[0].update(reason=None),
    lambda rows: rows[0].update(label='probably-ad'),
    lambda rows: rows[0].update(confidence=float('nan')),
    lambda rows: rows[0].update(confidence=True),
    lambda rows: rows[0].update(unrequested_field='extra'),
])
def test_incomplete_or_unsupported_second_pass_discards_all_proposals(mutate):
    def change(stage, answer, body):
        if stage == 'boundaries':
            mutate(answer['decisions'])
    provider = Provider('commercial', mutate=change)
    with patch('castwell.processing._classifier_request', side_effect=provider):
        with pytest.raises(processing.ProcessingError, match='incomplete decisions or unsupported evidence'):
            classify(transcript('A real paid promotion.'))
    assert len(provider.calls) == 2
    assert all(response.closed for response in provider.responses)


@pytest.mark.parametrize('body', [
    {'choices': []}, {'choices': [None]}, {'choices': ['not an object']},
    {'choices': [{'finish_reason': 'length', 'message': {'content': '{"decisions":[]}'}}]},
    {'choices': [{'message': {'content': '<think>unfinished reasoning with private-key'}}]},
])
def test_malformed_or_truncated_provider_responses_fail_cleanly(body):
    response = Response(body)
    with patch('castwell.processing._classifier_request', return_value=response):
        with pytest.raises(processing.ProcessingError) as error:
            classify(transcript('A sentence.'))
    assert response.closed
    assert 'private-key' not in str(error.value)


def test_provider_transport_failure_never_repeats_private_key_or_url():
    secret = 'https://secret-user:private-key@provider.invalid/v1'
    with patch('castwell.processing._classifier_request', side_effect=requests.Timeout(secret)):
        with pytest.raises(processing.ProcessingError) as error:
            classify(transcript('A sentence.'), config={'ai_key': 'private-key'})
    assert 'private-key' not in str(error.value)
    assert 'provider.invalid' not in str(error.value)


def test_cancellation_before_request_performs_no_inference():
    with patch('castwell.processing._classifier_request') as request:
        with pytest.raises(processing.ProcessingCancelled):
            classify(transcript('A sentence.'), should_cancel=lambda: True)
    request.assert_not_called()


def test_cancellation_after_first_response_closes_it_and_skips_second_pass():
    cancelled = False
    provider = Provider('commercial')
    def request(*args, **kwargs):
        nonlocal cancelled
        response = provider(*args, **kwargs)
        cancelled = True
        return response
    with patch('castwell.processing._classifier_request', side_effect=request):
        with pytest.raises(processing.ProcessingCancelled):
            classify(transcript('A sentence.'), should_cancel=lambda: cancelled)
    assert len(provider.calls) == 1
    assert provider.responses[0].closed


def test_recovered_gap_speech_keeps_review_requirement_after_sentence_split():
    source = aligned_transcript()
    source["segments"][0]["asr_recovered"] = True
    provider = Provider(lambda _, identifier: "commercial" if identifier == 1 else "editorial")
    with patch("castwell.processing._classifier_request", side_effect=provider):
        cuts = classify(source)
    assert [(cut["start"], cut["end"]) for cut in cuts] == [(2, 4)]
    assert cuts[0]["approved"] is False
    assert cuts[0]["requires_review"] is True
    assert "recovered" in cuts[0]["reason"]


def test_coarse_mixed_parent_preserves_real_timing_and_negative_child_audit():
    source = transcript('Use code SAVE for a discount. Now the science interview resumes.')
    source['segments'][0]['id'] = 41
    before = deepcopy(source)
    provider = Provider(lambda _, identifier: 'commercial' if identifier == 0 else 'editorial')
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(source)
    assert source == before
    assert [(cut['start'], cut['end']) for cut in cuts] == [(0, 2)]
    assert cuts[0]['approved'] is False
    assert cuts[0]['requires_review'] is True
    assert cuts[0]['label'] == 'mixed'
    assert [row['intent']['label'] for row in cuts[0]['verification']] == ['commercial', 'editorial']
    assert {row['parent_segment_id'] for row in cuts[0]['verification']} == {41}


def test_all_commercial_coarse_children_can_approve_the_original_span():
    provider = Provider('commercial')
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(transcript('Our sponsor makes this tool. Use code SAVE for ten percent off.'))
    assert [(cut['start'], cut['end'], cut['approved']) for cut in cuts] == [(0, 2, True)]
    assert len(cuts[0]['verification']) == 2


def test_truncated_output_subdivision_keeps_context_and_complete_unit_coverage():
    def truncate_large_targets(stage, answer, body):
        if len(answer['decisions']) > 2:
            body['choices'][0]['finish_reason'] = 'length'
    provider = Provider('commercial', mutate=truncate_large_targets)
    source = transcript(*[f'Commercial sentence {index}.' for index in range(8)])
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(source)
    assert [(cut['start'], cut['end']) for cut in cuts] == [(0, 16)]
    assert {row['segment_id'] for row in cuts[0]['verification']} == set(range(8))
    assert {len(context['target_ids']) for _, context, *_ in provider.calls} == {8, 4, 2}
    assert all([row['id'] for row in context['context']] == list(range(8))
               for _, context, *_ in provider.calls)
    assert all(response.closed for response in provider.responses)


def test_twenty_four_targets_reach_singletons_when_every_multi_target_response_is_invalid():
    def invalid_multiple_targets(stage, answer, body):
        if len(answer['decisions']) > 1:
            body['choices'][0]['finish_reason'] = 'length'

    provider = Provider('commercial', mutate=invalid_multiple_targets)
    source = transcript(*[f'Commercial sentence {index}.' for index in range(24)])
    original = deepcopy(source)
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(source)
    assert source == original
    assert [(cut['start'], cut['end']) for cut in cuts] == [(0, 48)]
    assert [row['segment_id'] for row in cuts[0]['verification']] == list(range(24))
    for stage in ('intent', 'boundaries'):
        singletons = [context['target_ids'][0] for kind, context, *_ in provider.calls
                      if kind == stage and len(context['target_ids']) == 1]
        assert singletons == list(range(24))
    assert {len(context['target_ids']) for _, context, *_ in provider.calls} == {24, 12, 6, 3, 2, 1}
    assert len(provider.calls) == 71  # 23 invalid branch requests, then two passes for each leaf.
    assert all([row['id'] for row in context['context']] == list(range(24))
               for _, context, *_ in provider.calls)
    assert all(response.closed for response in provider.responses)


def test_invalid_final_singleton_discards_previously_completed_targets():
    def fail_final_target(stage, answer, body):
        rows = answer['decisions']
        if len(rows) > 1:
            body['choices'][0]['finish_reason'] = 'length'
        elif rows[0]['id'] == 23 and stage == 'boundaries':
            rows[0]['evidence_id'] = 999

    provider = Provider('commercial', mutate=fail_final_target)
    source = transcript(*[f'Commercial sentence {index}.' for index in range(24)])
    original = deepcopy(source)
    with patch('castwell.processing._classifier_request', side_effect=provider):
        with pytest.raises(processing.ProcessingError, match='no new cuts were saved'):
            classify(source)
    assert source == original
    assert len(provider.calls) == 71
    assert provider.calls[-1][0] == 'boundaries'
    assert provider.calls[-1][1]['target_ids'] == [23]
    assert all(response.closed for response in provider.responses)


def test_persistently_invalid_output_has_bounded_subdivision_and_no_partial_result():
    def invalid(stage, answer, body):
        answer['decisions'][0]['evidence'] = 'fabricated quotation absent from transcript'
    provider = Provider('commercial', mutate=invalid)
    with patch('castwell.processing._classifier_request', side_effect=provider):
        with pytest.raises(processing.ProcessingError):
            classify(transcript(*[f'Commercial sentence {index}.' for index in range(24)]))
    assert [len(context['target_ids']) for _, context, *_ in provider.calls] == [24, 12, 6, 3, 1]
    assert all(response.closed for response in provider.responses)


def test_transport_failures_do_not_trigger_semantic_subdivision():
    with patch('castwell.processing._classifier_request', side_effect=requests.Timeout('timeout')) as request:
        with pytest.raises(processing.ProcessingError, match='request failed'):
            classify(transcript('First sentence.', 'Second sentence.'))
    assert request.call_count == 1


def test_cancellation_after_invalid_output_prevents_subdivision_requests():
    cancelled = False
    def invalid(stage, answer, body):
        body['choices'][0]['finish_reason'] = 'length'
    def progress(message):
        nonlocal cancelled
        if message.startswith('Retrying smaller'):
            cancelled = True
    provider = Provider('commercial', mutate=invalid)
    with patch('castwell.processing._classifier_request', side_effect=provider):
        with pytest.raises(processing.ProcessingCancelled):
            classify(transcript('First sentence.', 'Second sentence.'),
                     should_cancel=lambda: cancelled, progress=progress)
    assert len(provider.calls) == 1
    assert provider.responses[0].closed


@pytest.mark.parametrize('text,pieces', [
    ('Use code SAVE. now back to our story.', ['Use code SAVE.', 'now back to our story.']),
    ('Use code SAVE. “Today we discuss science.”', ['Use code SAVE.', '“Today we discuss science.”']),
    ('“Use code SAVE.” Now back to the interview.', ['“Use code SAVE.”', 'Now back to the interview.']),
    ('请用优惠码。现在讨论科学。', ['请用优惠码。', '现在讨论科学。']),
])
def test_coarse_sentence_roles_split_across_case_quotes_and_cjk(text, pieces):
    provider = Provider(lambda _, identifier: 'commercial' if identifier == 0 else 'editorial')
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(transcript(text))
    assert [row['text'] for row in provider.calls[0][1]['context']] == pieces
    assert cuts[0]['approved'] is False
    assert cuts[0]['requires_review'] is True
    assert cuts[0]['label'] == 'mixed'
    assert len(cuts[0]['verification']) == 2


def test_coarse_sentence_split_preserves_titles_and_abbreviations():
    provider = Provider('editorial')
    with patch('castwell.processing._classifier_request', side_effect=provider):
        assert classify(transcript('Dr. Stone studied rocks. The findings were surprising.')) == []
    assert [row['text'] for row in provider.calls[0][1]['context']] == [
        'Dr. Stone studied rocks.', 'The findings were surprising.']


@pytest.mark.parametrize("score", [3, 4, -1])
def test_ordinal_or_negative_confidence_is_rejected(score):
    with patch("castwell.processing._classifier_request", side_effect=Provider("commercial", confidence=score)):
        with pytest.raises(processing.ProcessingError):
            classify(transcript("A paid promotion."))


def test_schema_enforces_confidence_vocabulary_and_explicit_scale():
    provider = Provider()
    with patch("castwell.processing._classifier_request", side_effect=provider):
        classify(transcript("Editorial conversation."))
    payload = provider.calls[0][2]
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["decisions"]["items"]["properties"]["confidence"]["enum"] == [0, .5, .8, .9, .95, .99, 1]
    assert "number from 0 to 1" in payload["messages"][0]["content"]
    assert payload["chat_template_kwargs"]["enable_thinking"] is False


def test_reasoning_is_explicit_bounded_and_preserves_manual_approval():
    provider = Provider('commercial')
    with patch('castwell.processing._classifier_request', side_effect=provider):
        cuts = classify(transcript('A paid promotion.'), config={'ai_reasoning': True, 'review_only': True})
    for _, _, payload, *_ in provider.calls:
        assert payload['chat_template_kwargs'] == {'enable_thinking': True}
        assert payload['reasoning_budget_tokens'] == 1024
        assert payload['reasoning_format'] == 'deepseek'
        assert payload['max_tokens'] == 4096
        assert payload['temperature'] == 1.0
        assert payload['top_p'] == .95
        assert payload['response_format']['json_schema']['strict'] is True
    assert cuts[0]['inference_profile'] == 'qwen35-reasoning-1024'
    assert cuts[0]['requires_review'] is False
    assert cuts[0]['approved'] is False


@pytest.mark.parametrize('value', ['false', 1, None])
def test_invalid_reasoning_flag_never_reaches_provider(value):
    with patch('castwell.processing._classifier_request') as request:
        with pytest.raises(ValueError, match='reasoning'):
            classify(transcript('Some speech.'), config={'ai_reasoning': value})
    request.assert_not_called()


def test_reasoning_cannot_be_silently_ignored_by_legacy_ai():
    with patch('castwell.processing._classifier_request') as request:
        with pytest.raises(ValueError, match='verified'):
            classify(transcript('Some speech.'), config={'ai_reasoning': True, 'ai_policy': 'legacy'})
    request.assert_not_called()
