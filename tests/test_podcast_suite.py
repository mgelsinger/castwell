"""Production-facing show policies, batch reports, and bounded audio exports."""

import io
import asyncio
import json
from pathlib import Path
import sqlite3
import threading
import time
import zipfile

from fastapi.testclient import TestClient
import pytest

from castwell.app import create_app, ArchiveRequest
from castwell.jobs import Jobs
from castwell.library import Library


def entry(episode_id, show='A show', **values):
    return {'id': episode_id, 'title': f'Episode {episode_id}', 'podcast': show,
            'description': 'An episode', 'feed_url': f'https://podcasts.example/{show.replace(" ", "")}.xml',
            'media_url': 'https://podcasts.example/audio.mp3?token=private-media',
            'image': 'https://podcasts.example/art.jpg', 'duration': 12, 'published': '2026-10-10', **values}


@pytest.fixture
def suite(tmp_path, monkeypatch):
    for name in ('CASTWELL_TRANSCRIPTION_MODEL', 'CASTWELL_MODEL_CACHE', 'CASTWELL_AI_BASE_URL',
                 'CASTWELL_AI_MODEL', 'CASTWELL_AI_KEY'):
        monkeypatch.setenv(name, '')
    monkeypatch.setenv('CASTWELL_ALLOWED_HOSTS', 'testserver')
    app = create_app(tmp_path)
    app.state.library.import_entries([entry('remove'), entry('keep', 'B show'),
                                      entry('upload', 'Local uploads', feed_url='', media_url='', source='local')])
    with TestClient(app) as client:
        yield app, client


def hold_worker(jobs):
    entered, release = threading.Event(), threading.Event()
    def hold():
        entered.set()
        release.wait(10)
    jobs.pool.submit(hold)
    assert entered.wait(2)
    return release


def prepared(library, episode_id, cuts=None, cleaned=False, status='ready', analysis=True):
    folder = library.directory(episode_id)
    original = folder / 'original.mp3'
    original.write_bytes(('original-' + episode_id).encode())
    edited = folder / 'cleaned.mp3'
    if cleaned:
        edited.write_bytes(('cleaned-' + episode_id).encode())
    library.update(episode_id, audio=str(original), cleaned=str(edited) if cleaned else None,
                   cuts=cuts or [], duration=12, status=status, analysis_done=analysis)
    return original, edited


def test_catalogue_policy_persists_unsubscribe_and_local_shelf(suite):
    app, client = suite
    library = app.state.library
    shows = client.get('/api/podcasts').json()['podcasts']
    assert len(shows) == 3
    show = next(show for show in shows if show['title'] == 'B show')
    assert show['ad_mode'] == 'remove' and show['subscribed']
    assert client.patch(f'/api/podcasts/{show["id"]}', json={'ad_mode': 'keep'}).status_code == 200
    assert client.delete(f'/api/feeds/{show["id"]}').status_code == 200
    reopened = Library(library.root)
    retained = next(record for record in reopened.podcasts() if record['id'] == show['id'])
    assert retained['ad_mode'] == 'keep' and retained['episode_count'] == 1
    assert not retained['subscribed']
    assert reopened.get('keep')['ad_mode'] == 'keep'
    local = next(show for show in reopened.podcasts() if show['source'] == 'local')
    assert local['title'] == 'Local uploads' and not local['subscribed']


def test_single_refresh_does_not_restore_subscription_removed_during_fetch(suite, monkeypatch):
    app, client = suite
    library = app.state.library
    feed_id = library.get('remove')['feed_id']
    fetching, release = threading.Event(), threading.Event()
    response = []

    def fetch(_url):
        fetching.set()
        assert release.wait(5)
        return [entry('new-episode')]

    monkeypatch.setattr('castwell.app.read_feed', fetch)
    request = threading.Thread(target=lambda: response.append(client.post(f'/api/feeds/{feed_id}/refresh')))
    request.start()
    try:
        assert fetching.wait(5)
        assert client.delete(f'/api/feeds/{feed_id}').status_code == 200
    finally:
        release.set()
        request.join(timeout=5)
    assert not request.is_alive()
    assert response[0].status_code == 404
    assert all(feed['id'] != feed_id for feed in library.feeds())
    with pytest.raises(KeyError):
        library.get('new-episode')
    assert library.get('remove')['title'] == 'Episode remove'


def test_legacy_migration_preserves_audio_reviews_and_saved_subscription_removal(tmp_path):
    library = Library(tmp_path)
    library.import_entries([entry('legacy')])
    original, _ = prepared(library, 'legacy', [{'start': 2, 'end': 3, 'approved': False}], status='review')
    library.save_revision('legacy', [{'start': 1, 'end': 2, 'approved': True}], 'Previous edit')
    feed_id = library.get('legacy')['feed_id']
    library.delete_feed(feed_id)
    with sqlite3.connect(library.db) as db:
        db.execute('DROP TABLE podcasts')
        db.execute("DELETE FROM migrations WHERE name='podcast-preferences-v1'")
        db.execute('UPDATE episodes SET podcast_id=NULL')
    reopened = Library(tmp_path)
    assert reopened.feeds() == []
    assert reopened.get('legacy')['podcast_id'] == feed_id
    assert reopened.get('legacy')['cuts'][0]['approved'] is False
    assert len(reopened.revisions('legacy')) == 1
    assert original.read_bytes() == b'original-legacy'


def test_mixed_batch_inherits_policy_and_freezes_queued_choices(suite):
    app, client = suite
    library, jobs = app.state.library, app.state.jobs
    keep_show = library.get('keep')['podcast_id']
    client.patch(f'/api/podcasts/{keep_show}', json={'ad_mode': 'keep'})
    release = hold_worker(jobs)
    try:
        result = client.post('/api/process', json={'ids': ['remove', 'keep', 'keep', 'missing']}).json()
        assert result['queued'] == 2 and result['failed'] == 1
        assert library.get('remove')['pending_job']['options']['remove_ads'] is True
        assert library.get('keep')['pending_job']['options']['remove_ads'] is False
        client.patch(f'/api/podcasts/{keep_show}', json={'ad_mode': 'remove'})
        assert library.get('keep')['pending_job']['options']['remove_ads'] is False
        queue = client.get('/api/jobs').json()['jobs']
        assert next(item for item in queue if item['episode_id'] == 'keep')['ad_mode'] == 'keep'
        repeat = client.post('/api/process', json={'ids': ['keep']}).json()
        assert repeat['queued'] == 0 and repeat['skipped'] == 1
        jobs.cancel('remove')
        jobs.cancel('keep')
    finally:
        release.set()


@pytest.mark.parametrize('override', [True, False])
def test_explicit_process_override_wins_show_policy(suite, override):
    app, client = suite
    library, jobs = app.state.library, app.state.jobs
    library.set_podcast_mode([library.get('remove')['podcast_id']], 'keep' if override else 'remove')
    release = hold_worker(jobs)
    try:
        response = client.post('/api/process', json={'ids': ['remove'], 'remove_ads': override})
        assert response.status_code == 202
        assert library.get('remove')['pending_job']['options']['remove_ads'] is override
        jobs.cancel('remove')
    finally:
        release.set()


def test_retry_after_restart_preserves_snapshot_and_approval_policy(tmp_path):
    library = Library(tmp_path)
    library.import_entries([entry('one')])
    show_id = library.get('one')['podcast_id']
    library.set_podcast_mode([show_id], 'keep')
    jobs = Jobs(library, 'base', lambda: {'review_only': True, 'auto_approve_threshold': .9})
    release = hold_worker(jobs)
    try:
        assert jobs.submit('one', 'process')
        jobs.cancel('one')
        request = library.get('one')['pending_job']
        assert request['options']['remove_ads'] is False
    finally:
        release.set()
        jobs.shutdown()
    reopened = Library(tmp_path)
    reopened.set_podcast_mode([show_id], 'remove')
    restarted = Jobs(reopened, 'base', lambda: {'review_only': False, 'auto_approve_threshold': 0})
    release = hold_worker(restarted)
    try:
        assert restarted.submit('one', 'process')
        pending = reopened.get('one')['pending_job']
        assert pending['options']['remove_ads'] is False
        assert pending['approval_policy'] == {'review_only': True, 'auto_approve_threshold': .9}
        restarted.cancel('one')
    finally:
        release.set()
        restarted.shutdown()


def test_keep_process_skips_detection_and_retains_existing_decisions(suite, monkeypatch):
    app, _ = suite
    library, jobs = app.state.library, app.state.jobs
    cuts = [{'start': 2, 'end': 4, 'approved': True, 'source': 'manual'}]
    original, edited = prepared(library, 'keep', cuts, cleaned=True)
    transcript = {'duration': 12, 'segments': [{'start': 0, 'end': 12, 'text': 'Original podcast and ad read.'}]}
    library.update('keep', transcript=transcript, waveform={'duration': 12, 'peaks': [.1]})
    monkeypatch.setattr('castwell.processing.probe_duration', lambda audio: 12)
    monkeypatch.setattr('castwell.processing.detect_ads', lambda *args, **kwargs: pytest.fail('Keep mode ran ad detection'))
    monkeypatch.setattr('castwell.processing.transcribe', lambda *args, **kwargs: pytest.fail('Existing transcript was discarded'))
    jobs._process('keep', {'remove_ads': False}, {}, threading.Event())
    after = library.get('keep')
    assert after['cuts'] == cuts and after['cleaned'] == str(edited)
    assert [{key: segment[key] for key in ('start', 'end', 'text')} for segment in after['transcript']['segments']] == transcript['segments']
    assert original.read_bytes() == b'original-keep'
    assert after['status'] == 'ready'


def test_batch_prevalidation_cannot_partly_queue_and_reports_missing_ids(suite):
    app, client = suite
    result = client.post('/api/process', json={'ids': ['remove', 'keep'], 'download_only': True, 'redetect': True})
    assert result.status_code == 400
    assert app.state.jobs.pending == set()
    assert client.post('/api/process', json={'ids': ['remove'], 'remove_ads': 'false'}).status_code == 422
    for endpoint in ('/api/download', '/api/render', '/api/episodes/batch/retry'):
        response = client.post(endpoint, json={'ids': ['missing']})
        assert response.status_code == 202
        assert response.json()['failed'] == 1 and response.json()['queued'] == 0


def test_batch_state_is_explicit_strict_and_reports_missing(suite):
    app, client = suite
    response = client.patch('/api/episodes/batch/state', json={'ids': ['remove', 'keep', 'missing'], 'favorite': True, 'played': True})
    assert response.status_code == 200
    assert response.json()['updated'] == 2 and response.json()['failed'] == 1
    assert app.state.library.get('remove')['favorite'] and app.state.library.get('keep')['played']
    before = app.state.library.get('remove')['archived']
    assert client.patch('/api/episodes/batch/state', json={'ids': ['remove'], 'archived': 'false'}).status_code == 422
    assert client.patch('/api/episodes/batch/state', json={'ids': ['remove'], 'position': 2}).status_code == 422
    assert app.state.library.get('remove')['archived'] == before
    assert client.patch('/api/episodes/batch/state', json={'ids': ['remove']}).status_code == 400


def test_batch_cancel_and_retry_report_action_per_episode(suite):
    app, client = suite
    release = hold_worker(app.state.jobs)
    try:
        client.post('/api/process', json={'ids': ['remove'], 'remove_ads': False})
        result = client.post('/api/episodes/batch/cancel', json={'ids': ['remove', 'keep', 'missing']}).json()
        assert (result['cancelled'], result['skipped'], result['failed']) == (1, 1, 1)
        result = client.post('/api/episodes/batch/retry', json={'ids': ['remove', 'keep', 'missing']}).json()
        assert (result['queued'], result['skipped'], result['failed']) == (1, 1, 1)
        assert app.state.library.get('remove')['pending_job']['options']['remove_ads'] is False
        app.state.jobs.cancel('remove')
    finally:
        release.set()


def test_zip_prefers_per_show_policy_reports_omissions_and_cleans_workspace(suite):
    app, client = suite
    library = app.state.library
    cuts = [{'start': 2, 'end': 3, 'approved': True}, {'start': 5, 'end': 6, 'approved': False}]
    prepared(library, 'remove', cuts, cleaned=True, status='review')
    prepared(library, 'keep', cuts, cleaned=True, status='review')
    library.set_podcast_mode([library.get('keep')['podcast_id']], 'keep')
    response = client.post('/api/export/archive', json={'ids': ['remove', 'keep', 'upload', 'missing']})
    assert response.status_code == 200 and response.headers['content-type'] == 'application/zip'
    assert response.headers['x-castwell-included'] == '1'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        report = json.loads(archive.read('report.json'))
        assert report['included_count'] == 1 and report['omitted_count'] == 3
        remove = next(item for item in report['omitted'] if item['episode_id'] == 'remove')
        keep = next(item for item in report['included'] if item['episode_id'] == 'keep')
        assert 'review' in remove['detail']
        assert keep['version'] == 'original' and keep['approved_cut_count'] == 0
        assert archive.read(keep['file']) == b'original-keep'
        assert 'private-media' not in archive.read('report.json').decode()
        assert str(library.root) not in archive.read('report.json').decode()
    assert list((library.root / '.exports').iterdir()) == []
    # Explicit cleaned exports remain available as manual previews, without
    # entering the preferred completed batch or podcast feed.
    response = client.post('/api/export/archive', json={'ids': ['remove'], 'version': 'cleaned'})
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        report = json.loads(archive.read('report.json'))
        assert report['included'][0]['version'] == 'cleaned'
        assert report['included'][0]['pending_review'] is True
        assert archive.read(report['included'][0]['file']) == b'cleaned-remove'
    assert '<guid isPermaLink="false">remove</guid>' not in client.get('/api/export/feed').text


def test_coverage_warnings_block_remove_completion_but_keep_original_is_ready(suite):
    app, client = suite
    library = app.state.library
    cuts = [{'start': 1, 'end': 2, 'approved': True}]
    transcript = {'duration': 12, 'segments': [
        {'id': 0, 'start': 0, 'end': 2, 'text': 'Opening.'},
        {'id': 1, 'start': 7, 'end': 12, 'text': 'Return.'}]}
    for identifier in ('remove', 'keep'):
        prepared(library, identifier, cuts, cleaned=True)
        library.update(identifier, transcript=transcript)
    prepared(library, 'upload', cuts=[])
    library.update('upload', transcript=transcript)
    library.set_podcast_mode([library.get('keep')['podcast_id']], 'keep')
    summaries = {episode['id']: episode for episode in library.summaries()}
    assert summaries['remove']['status'] == 'review'
    assert summaries['remove']['preferred_version'] == 'original'
    assert summaries['remove']['export_ready'] is False
    assert summaries['keep']['status'] == 'ready'
    assert summaries['keep']['preferred_version'] == 'original'
    assert summaries['keep']['export_ready'] is True
    response = client.post('/api/export/archive', json={'ids': ['remove', 'keep', 'upload']})
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        report = json.loads(archive.read('report.json'))
        assert [item['episode_id'] for item in report['included']] == ['keep']
        assert [item['episode_id'] for item in report['omitted']] == ['remove', 'upload']
        assert archive.read(report['included'][0]['file']) == b'original-keep'
    feed = client.get('/api/export/feed').text
    assert '<guid isPermaLink="false">remove</guid>' not in feed
    assert '<guid isPermaLink="false">upload</guid>' not in feed
    assert '<guid isPermaLink="false">keep</guid>' in feed
    # Warnings also survive reopening and lightweight reads used by RSS.
    reopened = Library(library.root)
    assert reopened.get('remove', include_large=False)['transcript_review'] is True


def test_zip_form_streams_native_download_and_strips_imported_transcript_metadata(suite):
    app, client = suite
    library = app.state.library
    prepared(library, 'remove')
    library.update('remove', transcript={'duration': 12, 'provider_key': 'private-key', 'media_url': 'https://secret.example',
                   'segments': [{'start': 0, 'end': 12, 'text': 'Episode.', 'secret': 'hidden'}]})
    response = client.post('/api/export/archive/form', data={'payload': json.dumps({'ids': ['remove'], 'include_transcripts': True})})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        report = json.loads(archive.read('report.json'))
        transcript = archive.read(report['included'][0]['transcript_file']).decode()
        assert 'Episode.' in transcript
        assert 'private-key' not in transcript and 'secret.example' not in transcript and 'hidden' not in transcript
    assert client.post('/api/export/archive/form', data={'payload': '{bad'}).status_code == 400
    assert client.get('/').headers['referrer-policy'] == 'same-origin'
    assert client.post('/api/export/archive/form', data={'payload': json.dumps({'ids': ['remove']})}, headers={'Origin': 'null'}).status_code == 403
    assert client.post('/api/export/archive/form', data={'payload': json.dumps({'ids': ['remove']})}, headers={'Origin': 'https://untrusted.example'}).status_code == 403


@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
def test_archive_disconnect_or_send_failure_cleans_files_and_releases_capacity(suite, failure):
    app, client = suite
    library = app.state.library
    prepared(library, 'remove')
    endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', '') == '/api/export/archive')
    first, second = endpoint(ArchiveRequest(ids=['remove'])), endpoint(ArchiveRequest(ids=['remove']))
    assert client.post('/api/export/archive', json={'ids': ['remove']}).status_code == 429
    async def fail_send(message):
        raise failure('Simulated interrupted transfer')
    async def receive():
        return {'type': 'http.disconnect'}
    scope = {'type': 'http', 'method': 'GET', 'headers': [], 'asgi': {'spec_version': '2.4'}}
    with pytest.raises(failure):
        asyncio.run(first(scope, receive, fail_send))
    assert len(list((library.root / '.exports').iterdir())) == 1
    # A replacement export proves the failed transfer released its slot.
    assert client.post('/api/export/archive', json={'ids': ['remove']}).status_code == 200
    with pytest.raises(failure):
        asyncio.run(second(scope, receive, fail_send))
    assert list((library.root / '.exports').iterdir()) == []


def test_zip_limits_selection_and_reports_disk_space_before_publishing(suite, monkeypatch):
    app, client = suite
    library = app.state.library
    prepared(library, 'remove')
    prepared(library, 'keep')
    monkeypatch.setattr('castwell.app.MAX_ARCHIVE_AUDIO_BYTES', 20)
    response = client.post('/api/export/archive', json={'ids': ['remove', 'keep']})
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        report = json.loads(archive.read('report.json'))
        assert report['included_count'] == 1 and report['omitted_count'] == 1
        assert report['audio_bytes'] <= 20
    monkeypatch.setattr('castwell.app.shutil.disk_usage', lambda root: type('Usage', (), {'free': 0})())
    assert client.post('/api/export/archive', json={'ids': ['remove']}).status_code == 400
    assert list((library.root / '.exports').iterdir()) == []


@pytest.mark.parametrize('ids', [['remove', 'keep'], ['keep', 'remove']])
def test_zip_transcript_budget_reserves_later_audio_regardless_of_order(suite, monkeypatch, ids):
    app, client = suite
    library = app.state.library
    first, _ = prepared(library, 'remove')
    last, _ = prepared(library, 'keep')
    first.write_bytes(b'a' * 100)
    last.write_bytes(b'b' * 1800)
    library.update('remove', transcript={'duration': 12, 'segments': [{'start': 0, 'end': 12, 'text': 'x' * 500}]})
    monkeypatch.setattr('castwell.app.MAX_ARCHIVE_AUDIO_BYTES', 2048)
    response = client.post('/api/export/archive', json={'ids': ids, 'include_transcripts': True})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        report = json.loads(archive.read('report.json'))
        assert report['included_count'] == 2 and report['omitted_count'] == 0
        assert report['audio_bytes'] == 1900 and report['transcript_bytes'] == 0
        assert report['payload_bytes'] == 1900
        assert sum(item.file_size for item in archive.infolist() if item.filename != 'report.json') <= 2048
        item = next(item for item in report['included'] if item['episode_id'] == 'remove')
        assert 'transcript_omitted' in item and 'transcript_file' not in item
    assert first.read_bytes() == b'a' * 100 and last.read_bytes() == b'b' * 1800
    assert list((library.root / '.exports').iterdir()) == []


def test_zip_disk_reserve_includes_accepted_transcript_and_cleans_failure(suite, monkeypatch):
    app, client = suite
    library = app.state.library
    original, _ = prepared(library, 'remove')
    original.write_bytes(b'a' * 100)
    library.update('remove', transcript={'duration': 12, 'segments': [{'start': 0, 'end': 12, 'text': 'x' * 500}]})
    # This admits audio plus the fixed overhead, but not the duplicate
    # transcript bytes needed when disk snapshots are copied into the ZIP.
    monkeypatch.setattr('castwell.app.shutil.disk_usage', lambda root: type('Usage', (), {'free': 100 + 16 * 1024**2})())
    response = client.post('/api/export/archive', json={'ids': ['remove'], 'include_transcripts': True})
    assert response.status_code == 400 and 'disk space' in response.json()['detail']
    assert list((library.root / '.exports').iterdir()) == []
    assert original.read_bytes() == b'a' * 100
    assert client.post('/api/export/archive', json={'ids': ['remove'], 'include_transcripts': False}).status_code == 200


def test_render_requires_preparation_but_preserves_explicit_reviewed_empty_decision(suite):
    app, client = suite
    library = app.state.library
    prepared(library, 'remove', status='downloaded', analysis=False)
    result = client.post('/api/render', json={'ids': ['remove']}).json()
    assert result['queued'] == 0 and result['skipped'] == 1
    assert 'Prepare and review' in result['results'][0]['detail']
    assert library.get('remove')['status'] == 'downloaded'
    assert client.post('/api/episodes/remove/render').status_code == 400
    assert not app.state.jobs.pending
    assert client.post('/api/episodes/remove/cuts', json={'cuts': []}).status_code == 200
    result = client.post('/api/render', json={'ids': ['remove']}).json()
    assert result['queued'] == 1 and result['skipped'] == 0
    deadline = time.monotonic() + 2
    while app.state.jobs.pending and time.monotonic() < deadline:
        time.sleep(.01)
    record = library.get('remove')
    assert record['status'] == 'ready' and record['analysis_done']
    assert library.public(record)['export_ready']


def test_zip_omits_external_paths_and_unapproved_or_unprepared_audio(suite, tmp_path):
    app, client = suite
    library = app.state.library
    outside = tmp_path.parent / (tmp_path.name + '-outside.mp3')
    outside.write_bytes(b'external file')
    try:
        library.update('remove', audio=str(outside), analysis_done=True, status='ready')
        prepared(library, 'keep', [{'start': 2, 'end': 3, 'approved': False}], cleaned=True, status='review')
        response = client.post('/api/export/archive', json={'ids': ['remove', 'keep']})
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            report = json.loads(archive.read('report.json'))
            assert report['included_count'] == 0 and report['omitted_count'] == 2
            assert archive.namelist() == ['report.json']
            assert str(outside) not in archive.read('report.json').decode()
    finally:
        outside.unlink()


def test_catalogue_and_health_do_not_expose_private_feed_credentials(suite):
    app, client = suite
    library = app.state.library
    library.import_entries([entry('private', 'Private show', feed_url='https://user:pass@podcasts.example/private.xml?token=subscription-secret',
                                  image='https://podcasts.example/art.jpg?key=subscription-secret')])
    for endpoint in ('/api/podcasts', '/api/episodes', '/api/health'):
        response = client.get(endpoint)
        assert response.status_code == 200
        for secret in ('subscription-secret', 'private-media', 'user:pass', 'feed_url', 'media_url'):
            assert secret not in response.text
    health = client.get('/api/health').json()
    assert health['status'] == 'ok' and health['version'] and health['startup_time']
    assert health['queue']['total'] == 0


def test_gallery_preview_preserves_full_stored_description_and_detail_transcript(suite):
    app, client = suite
    library = app.state.library
    description = 'Podcast editorial notes. ' * 1000 + 'Full final paragraph.'
    library.import_entries([entry('remove', description=description)])
    transcript = {'duration': 12, 'segments': [{'start': 0, 'end': 12, 'text': 'Full spoken episode.'}]}
    library.update('remove', transcript=transcript)
    summary = next(record for record in client.get('/api/episodes').json()['episodes'] if record['id'] == 'remove')
    assert summary['description'] == description[:256] and summary['description_truncated']
    assert 'transcript' not in summary
    detail = client.get('/api/episodes/remove').json()
    assert detail['description'] == description
    assert detail['transcript']['segments'][0]['text'] == 'Full spoken episode.'
    reopened = Library(library.root)
    assert reopened.get('remove')['description'] == description
    assert reopened.get('remove')['transcript'] == transcript


def test_gallery_etag_unchanged_poll_is_empty_and_mutations_invalidate_it(suite):
    app, client = suite
    first = client.get('/api/episodes')
    assert first.status_code == 200 and first.headers['cache-control'] == 'private, no-cache'
    etag = first.headers['etag']
    unchanged = client.get('/api/episodes', headers={'If-None-Match': etag})
    assert unchanged.status_code == 304 and unchanged.content == b''
    assert unchanged.headers['etag'] == etag
    assert client.get('/api/episodes', headers={'If-None-Match': 'W/' + etag}).status_code == 304
    app.state.library.update('remove', favorite=True)
    changed = client.get('/api/episodes', headers={'If-None-Match': etag})
    assert changed.status_code == 200 and changed.headers['etag'] != etag
    latest = changed.headers['etag']
    assert client.patch('/api/settings', json={'language': 'en'}).status_code == 200
    assert client.get('/api/episodes', headers={'If-None-Match': latest}).status_code == 200


def test_gallery_etag_detects_original_audio_removed_outside_database(suite):
    app, client = suite
    original, _ = prepared(app.state.library, 'remove')
    first = client.get('/api/episodes')
    assert next(item for item in first.json()['episodes'] if item['id'] == 'remove')['has_audio']
    original.unlink()
    changed = client.get('/api/episodes', headers={'If-None-Match': first.headers['etag']})
    assert changed.status_code == 200
    assert not next(item for item in changed.json()['episodes'] if item['id'] == 'remove')['has_audio']


def test_bulk_refresh_is_async_reports_private_failures_and_preserves_modes(suite, monkeypatch):
    app, client = suite
    library = app.state.library
    keep_show = library.get('keep')['podcast_id']
    library.set_podcast_mode([keep_show], 'keep')
    entered, release = threading.Event(), threading.Event()
    def read_feed(url):
        entered.set()
        release.wait(3)
        if 'Bshow' in url:
            raise ValueError('https://user:secret@example.invalid/feed?token=private')
        return [entry('new')]
    monkeypatch.setattr('castwell.app.read_feed', read_feed)
    try:
        result = client.post('/api/feeds/refresh').json()
        assert result['started'] and result['total'] == 2
        assert entered.wait(1)
        assert client.post('/api/feeds/refresh').json()['started'] is False
    finally:
        release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = client.get('/api/feeds/refresh/status').json()
        if result['status'] == 'complete':
            break
        time.sleep(.01)
    assert result['status'] == 'complete' and result['completed'] == 2
    assert {item['status'] for item in result['results']} == {'updated', 'failed'}
    assert 'user:secret' not in json.dumps(result) and 'token=private' not in json.dumps(result)
    assert library.get('keep')['ad_mode'] == 'keep'
