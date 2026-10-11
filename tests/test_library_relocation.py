"""Portable library media follows its containing folder without DB mutation."""
import io
import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import zipfile

from fastapi.testclient import TestClient
import pytest

from castwell.app import create_app
from castwell.library import Library


def prepared_library(root):
    library = Library(root)
    library.import_entries([{'id': 'episode-one', 'title': 'Portable recording', 'podcast': 'Local uploads',
                             'feed_url': '', 'media_url': '', 'duration': 12}])
    folder = library.directory('episode-one')
    original, cleaned = folder / 'original.wav', folder / 'cleaned.mp3'
    original.write_bytes(b'original recording bytes')
    cleaned.write_bytes(b'approved cleaned recording bytes')
    transcript = {'duration': 12, 'segments': [{'start': 0, 'end': 12, 'text': 'Synthetic saved transcript'}]}
    cuts = [{'start': 3, 'end': 7, 'approved': True, 'source': 'manual'}]
    library.update('episode-one', audio=str(original), cleaned=str(cleaned), transcript=transcript,
                   cuts=cuts, duration=12, cleaned_duration=8, removed_seconds=4, analysis_done=True, status='ready')
    library.set_episode_flags('episode-one', favorite=True, position=4.5)
    library.save_revision('episode-one', cuts, 'Saved approved edit')
    return library, original, cleaned


def raw_paths(root):
    with closing(sqlite3.connect((root / 'library.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        return db.execute("SELECT audio,cleaned FROM episodes WHERE id='episode-one'").fetchone()


def test_copied_library_prefers_its_own_media_and_keeps_database_paths(tmp_path):
    library, original, cleaned = prepared_library(tmp_path / 'Original library')
    before = library.get('episode-one')
    persisted = raw_paths(library.root)
    copied = tmp_path / 'Copied library'
    shutil.copytree(library.root, copied)
    original.write_bytes(b'old library changed after copying')
    cleaned.write_bytes(b'old edited file changed after copying')

    reopened = Library(copied)
    episode = reopened.get('episode-one')
    assert Path(episode['audio']).read_bytes() == b'original recording bytes'
    assert Path(episode['cleaned']).read_bytes() == b'approved cleaned recording bytes'
    assert Path(episode['audio']).parent == copied / 'episodes/episode-one'
    assert reopened.public(episode)['has_audio'] and reopened.public(episode)['has_cleaned']
    for field in ('transcript', 'cuts', 'favorite', 'position', 'analysis_done', 'duration', 'cleaned_duration'):
        assert episode[field] == before[field]
    assert reopened.revisions('episode-one') == library.revisions('episode-one')
    assert raw_paths(copied) == persisted


@pytest.mark.parametrize('field,saved', [
    ('audio', 'external/original.wav'),
    ('audio', 'episodes/other-episode/original.wav'),
    ('audio', 'episodes/episode-one/recording.wav'),
    ('audio', 'episodes/episode-one/original.exe'),
    ('audio', 'episodes/episode-one/cleaned.mp3'),
    ('cleaned', 'episodes/episode-one/original.wav'),
])
def test_relocation_never_replaces_external_or_noncanonical_paths(tmp_path, field, saved):
    library, _, _ = prepared_library(tmp_path / 'Current library')
    external = str(tmp_path / 'Old folder' / saved)
    library.update('episode-one', **{field: external})
    assert library.get('episode-one')[field] == external
    # A relative suffix or traversal cannot be treated as an owned absolute path.
    library.update('episode-one', **{field: saved})
    assert library.get('episode-one')[field] == saved


def test_relocation_preserves_missing_files_and_rejects_traversal(tmp_path):
    library, original, _ = prepared_library(tmp_path / 'Current library')
    old = str(tmp_path / 'Old folder/episodes/episode-one/original.wav')
    original.unlink()
    library.update('episode-one', audio=old)
    assert library.get('episode-one')['audio'] == old
    original.write_bytes(b'current original')
    traversal = str(tmp_path / 'Old folder/../other/episodes/episode-one/original.wav')
    library.update('episode-one', audio=traversal)
    assert library.get('episode-one')['audio'] == traversal


@pytest.mark.parametrize('target_location', ['outside', 'other-recording'])
def test_relocation_cannot_follow_a_current_media_symlink(tmp_path, target_location):
    library, original, _ = prepared_library(tmp_path / 'Current library')
    target = (tmp_path / 'External recording.wav' if target_location == 'outside'
              else library.root / 'episodes/another-episode/original.wav')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'another recording')
    original.unlink()
    try:
        original.symlink_to(target)
    except OSError:
        pytest.skip('This Windows account cannot create test symlinks')
    old = str(tmp_path / 'Old folder/episodes/episode-one/original.wav')
    library.update('episode-one', audio=old)
    assert library.get('episode-one')['audio'] == old


def test_moved_library_serves_original_cleaned_and_zip_with_saved_review_state(tmp_path, monkeypatch):
    monkeypatch.setenv('CASTWELL_ALLOWED_HOSTS', 'testserver')
    library, _, _ = prepared_library(tmp_path / 'Original library')
    persisted = raw_paths(library.root)
    moved = tmp_path / 'Moved portable library'
    library.root.rename(moved)
    with TestClient(create_app(moved)) as client:
        episode = client.get('/api/episodes/episode-one').json()
        assert episode['has_audio'] and episode['has_cleaned'] and episode['has_transcript']
        assert episode['favorite'] is True and episode['position'] == 4.5
        assert episode['cuts'][0]['approved'] is True
        assert episode['transcript']['segments'][0]['text'] == 'Synthetic saved transcript'
        assert client.get('/media/episode-one/original').content == b'original recording bytes'
        assert client.get('/media/episode-one/cleaned').content == b'approved cleaned recording bytes'
        response = client.post('/api/export/archive', json={'ids': ['episode-one'], 'version': 'preferred'})
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            report = json.loads(archive.read('report.json'))
            assert report['included_count'] == 1 and report['omitted_count'] == 0
            assert archive.read(report['included'][0]['file']) == b'approved cleaned recording bytes'
    assert raw_paths(moved) == persisted
