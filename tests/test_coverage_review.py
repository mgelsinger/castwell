"""Explicit listener resolution and GPU admission guards, without inference."""
from copy import deepcopy
import io
import json
from unittest.mock import Mock
import zipfile

from fastapi.testclient import TestClient
import pytest

from castwell.app import create_app
from castwell.library import Library
from castwell.transcript_quality import analyze_transcript


@pytest.fixture
def app_case(tmp_path, monkeypatch):
    for variable in ('CASTWELL_AI_BASE_URL', 'CASTWELL_AI_MODEL', 'CASTWELL_AI_KEY', 'CASTWELL_AI_POLICY',
                     'CASTWELL_TRANSCRIPTION_MODEL', 'CASTWELL_MODEL_CACHE'):
        monkeypatch.setenv(variable, '')
    monkeypatch.setenv('CASTWELL_ALLOWED_HOSTS', 'testserver')
    app = create_app(tmp_path)
    library = app.state.library
    library.import_entries([{'id': 'episode', 'title': 'An episode', 'podcast': 'A podcast', 'duration': 20,
                             'media_url': '', 'feed_url': '', 'source': 'local'}])
    original = library.directory('episode') / 'original.mp3'
    original.write_bytes(b'owned recording; decoder is mocked')
    transcript = {'duration': 20, 'language': 'en', 'segments': [
        {'id': 0, 'start': 0, 'end': 4, 'text': 'Opening.'},
        {'id': 1, 'start': 14, 'end': 20, 'text': 'Return.'}]}
    transcript['quality'] = analyze_transcript(transcript)
    transcript['quality']['recovery'] = {'failed_attempts': 1, 'attempts': [{'status': 'failed'}]}
    transcript['quality']['audio_coverage'] = {'generic_gap_review_cleared': False, 'human_listening_verified': False}
    cuts = [{'start': 4, 'end': 7, 'approved': True, 'source': 'manual'}]
    library.update('episode', audio=str(original), transcript=transcript, cuts=cuts,
                   duration=20, analysis_done=True, status='review')
    with TestClient(app) as client:
        yield app, client, original, deepcopy(transcript)


def acknowledge(client, token=None, **values):
    if token is None:
        token = client.get('/api/episodes/episode').json()['coverage_review_token']
    return client.post('/api/episodes/episode/coverage-review', json={'reviewed': True, 'review_token': token, **values})


def test_explicit_review_preserves_evidence_and_requires_a_separate_render(app_case, monkeypatch):
    app, client, original, transcript = app_case
    render = Mock()
    monkeypatch.setattr('castwell.processing.render_audio', render)
    token = client.get('/api/episodes/episode').json()['coverage_review_token']
    assert isinstance(token, str) and len(token) == 64
    result = acknowledge(client, token)
    assert result.status_code == 200
    reviewed = result.json()
    assert reviewed['transcript']['segments'] == transcript['segments']
    quality = reviewed['transcript']['quality']
    for field in ('gaps', 'warning', 'recovery', 'audio_coverage'):
        assert quality[field] == transcript['quality'][field]
    assert quality['requires_review'] is False and reviewed['transcript_review'] is False
    assert quality['user_review']['listener_reported_listening'] is True
    assert quality['user_review']['model_verified'] is False
    assert quality['user_review']['human_listening_verified'] is False
    assert reviewed['coverage_review_acknowledged'] is True
    assert reviewed['coverage_review_token'] == token
    assert reviewed['status'] == 'review' and reviewed['export_ready'] is False
    render.assert_not_called()
    def export(audio, output, cuts, **options):
        output.write_bytes(b'explicitly rendered audio')
        return {'duration': 17, 'removed_seconds': 3}
    monkeypatch.setattr('castwell.processing.render_audio', export)
    app.state.jobs.render('episode')
    complete = client.get('/api/episodes/episode').json()
    assert complete['status'] == 'ready' and complete['export_ready'] is True
    assert complete['preferred_version'] == 'cleaned'
    response = client.post('/api/export/archive', json={'ids': ['episode']})
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert json.loads(archive.read('report.json'))['included'][0]['version'] == 'cleaned'
    assert '<guid isPermaLink="false">episode</guid>' in client.get('/api/export/feed').text
    summary = client.get('/api/episodes').json()['episodes'][0]
    assert 'coverage_review_token' not in summary and token not in json.dumps(summary)
    assert token not in client.get('/api/export/feed').text


@pytest.mark.parametrize('confirmation,status', [(False, 400), ('true', 422), (1, 422), (None, 422)])
def test_review_requires_an_explicit_boolean_confirmation(app_case, confirmation, status):
    app, client, original, transcript = app_case
    assert acknowledge(client, reviewed=confirmation).status_code == status
    assert app.state.library.get('episode')['transcript']['quality']['requires_review'] is True


def test_coverage_acknowledgement_does_not_approve_unresolved_detector_judgments(app_case, monkeypatch):
    app, client, original, transcript = app_case
    cuts = [{'start': 4, 'end': 7, 'approved': False, 'requires_review': True, 'source': 'ai',
             'approval_metadata': {'global_requires_review': True, 'global_unresolved_unit_ids': [9]}}]
    app.state.library.update('episode', cuts=deepcopy(cuts), analysis_done=True)
    render = Mock()
    monkeypatch.setattr('castwell.processing.render_audio', render)
    result = acknowledge(client)
    assert result.status_code == 200
    assert result.json()['cuts'] == cuts and result.json()['transcript_review'] is False
    app.state.jobs.render('episode', automatic=True)
    detail = client.get('/api/episodes/episode').json()
    assert detail['status'] == 'review' and detail['export_ready'] is False
    assert detail['cuts'] == cuts and detail['has_cleaned'] is False
    render.assert_not_called()


def test_stale_original_token_is_rejected_and_changed_audio_invalidates_previous_review(app_case):
    app, client, original, transcript = app_case
    token = client.get('/api/episodes/episode').json()['coverage_review_token']
    original.write_bytes(b'a changed and larger owned recording')
    assert acknowledge(client, token).status_code == 409
    assert acknowledge(client).status_code == 200
    app.state.library.update('episode', status='ready')
    original.write_bytes(b'another longer audio version invalidates the listener acknowledgement')
    reviewed = client.get('/api/episodes/episode').json()
    assert reviewed['coverage_review_acknowledged'] is False
    assert reviewed['transcript']['quality']['requires_review'] is True
    assert reviewed['status'] == 'review' and reviewed['export_ready'] is False
    reopened = Library(app.state.library.root)
    assert reopened.get('episode', include_large=False)['transcript_review'] is True
    assert '<guid isPermaLink="false">episode</guid>' not in client.get('/api/export/feed').text


def test_transcript_import_invalidates_review_and_rejects_the_old_token(app_case):
    app, client, original, transcript = app_case
    token = client.get('/api/episodes/episode').json()['coverage_review_token']
    assert acknowledge(client, token).status_code == 200
    replacement = deepcopy(client.get('/api/episodes/episode').json()['transcript'])
    replacement['segments'][0]['text'] = 'A different transcript.'
    imported = client.post('/api/episodes/episode/transcript', json=replacement)
    assert imported.status_code == 200
    assert imported.json()['coverage_review_acknowledged'] is False
    assert imported.json()['transcript']['quality']['requires_review'] is True
    assert acknowledge(client, token).status_code == 409


def test_retranscription_without_user_review_clears_acknowledgement_but_can_resolve_coverage_automatically(app_case):
    app, client, original, transcript = app_case
    assert acknowledge(client).status_code == 200
    fresh = {'duration': 20, 'segments': [{'id': 0, 'start': 0, 'end': 20, 'text': 'A complete new transcript.'}]}
    fresh['quality'] = analyze_transcript(fresh)
    app.state.library.update('episode', transcript=fresh)
    detail = client.get('/api/episodes/episode').json()
    assert detail['coverage_review_acknowledged'] is False
    assert detail['transcript_review'] is False
    assert 'user_review' not in detail['transcript']['quality']


def test_reusing_the_same_validated_transcript_retains_explicit_acknowledgement(app_case):
    from castwell.processing import validate_transcript
    app, client, original, transcript = app_case
    reviewed = acknowledge(client).json()
    token = reviewed['coverage_review_token']
    app.state.library.update('episode', transcript=validate_transcript(reviewed['transcript']))
    current = client.get('/api/episodes/episode').json()
    assert current['coverage_review_token'] == token
    assert current['coverage_review_acknowledged'] is True
    assert current['transcript_review'] is False


def test_explicit_full_audio_review_and_saved_empty_decisions_can_finish_without_retranscription(app_case):
    app, client, original, transcript = app_case
    empty = {'duration': 20, 'segments': []}
    empty['quality'] = analyze_transcript(empty)
    app.state.library.update('episode', transcript=empty, cuts=[], analysis_done=True)
    assert acknowledge(client).status_code == 200
    app.state.jobs.render('episode')
    finished = client.get('/api/episodes/episode').json()
    assert finished['status'] == 'ready' and finished['export_ready'] is True
    assert finished['preferred_version'] == 'original'
    assert finished['transcript']['quality']['user_review']['model_verified'] is False


def test_missing_original_and_active_job_cannot_be_acknowledged(app_case):
    app, client, original, transcript = app_case
    token = client.get('/api/episodes/episode').json()['coverage_review_token']
    app.state.jobs.pending.add('episode')
    try:
        assert acknowledge(client, token).status_code == 409
    finally:
        app.state.jobs.pending.clear()
    original.unlink()
    assert acknowledge(client, token).status_code == 400
    assert client.get('/api/episodes/episode').json()['coverage_review_token'] is None


def test_classifier_test_rejects_queued_work_before_any_provider_call(app_case, monkeypatch):
    app, client, original, transcript = app_case
    check = Mock()
    monkeypatch.setattr(app.state.settings, 'test_classifier', check)
    app.state.jobs.pending.add('episode')
    try:
        assert client.post('/api/settings/test-classifier').status_code == 409
        check.assert_not_called()
        assert client.post('/api/models/prepare').status_code == 409
    finally:
        app.state.jobs.pending.clear()


def test_classifier_test_rejects_speech_model_preparation_before_provider_call(app_case, monkeypatch):
    app, client, original, transcript = app_case
    monkeypatch.setattr('castwell.app.ModelPreparation.get', lambda self: {'status': self.state['status']})
    captured = []
    def submit(self):
        self.state['status'] = 'running'
        captured.append(self)
        return True
    monkeypatch.setattr('castwell.app.ModelPreparation.submit', submit)
    check = Mock()
    monkeypatch.setattr(app.state.settings, 'test_classifier', check)
    assert client.post('/api/models/prepare').status_code == 202
    assert client.post('/api/settings/test-classifier').status_code == 409
    check.assert_not_called()
    captured[0].state['status'] = 'idle'


def test_episode_admission_waits_for_preparation_but_host_actions_and_callbacks_do_not_deadlock(app_case, monkeypatch):
    import threading
    import time
    from castwell.app import ModelPreparation
    app, client, original, transcript = app_case
    entered, release = threading.Event(), threading.Event()
    captured = []
    original_get = ModelPreparation.get
    def state(self):
        captured.append(self)
        return original_get(self)
    monkeypatch.setattr(ModelPreparation, 'get', state)
    assert client.get('/api/models/status').json()['status'] == 'idle'
    preparation = captured[0]
    def warm(selected, progress):
        progress('Offline preparation fixture')
        entered.set()
        if not release.wait(5):
            raise TimeoutError('Preparation fixture was not released')
        progress('Offline preparation fixture finished')
        return {'ok': True}
    monkeypatch.setattr('castwell.app.warm_transcription_model', warm)
    submit = Mock(return_value=True)
    monkeypatch.setattr(app.state.jobs, 'submit', submit)
    app.state.library.update('episode', status='error')
    try:
        assert client.post('/api/models/prepare').status_code == 202
        assert entered.wait(2)
        for endpoint, body in (('/api/episodes/episode/process', {}), ('/api/process', {'ids': ['episode']}),
                               ('/api/episodes/batch/retry', {'ids': ['episode']})):
            result = client.post(endpoint, json=body)
            assert result.status_code == 409 and 'speech model preparation' in result.json()['detail']
        submit.assert_not_called()
        assert app.state.jobs.pending == set()
        assert client.post('/api/download', json={'ids': ['episode']}).status_code == 202
        assert client.post('/api/episodes/episode/render').status_code == 202
        submit.assert_called_once_with('episode', 'render')
    finally:
        release.set()
    deadline = time.monotonic() + 2
    while client.get('/api/models/status').json()['status'] == 'running' and time.monotonic() < deadline:
        time.sleep(.01)
    assert client.get('/api/models/status').json()['status'] == 'ready'
    def locked_submit(*values):
        assert app.state.jobs.lock._is_owned()
        assert preparation.lock.acquire(blocking=False) is False
        return True
    submit.reset_mock()
    submit.side_effect = locked_submit
    assert client.post('/api/episodes/episode/process', json={}).status_code == 202
    assert client.post('/api/process', json={'ids': ['episode']}).status_code == 202
    assert client.post('/api/episodes/batch/retry', json={'ids': ['episode']}).status_code == 202
    assert submit.call_count == 3
