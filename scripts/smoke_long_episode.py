#!/usr/bin/env python3
"""Validate full-length batch audio handling with generated tones and no models.

The default run uses two 60-minute recordings in a temporary library. Exact
transcripts and cut boundaries are supplied by the fixture. This measures audio
processing, approval handling and exports, not speech or ad detection accuracy.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from unittest.mock import patch
import zipfile

import requests
import uvicorn


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from castwell.app import create_app
from castwell.library import Library


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def decoded_seconds(path, sample_rate=16000):
    """Count PCM bytes in bounded chunks, including MP3 gapless decoding."""
    command = ['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path), '-map', '0:a:0',
               '-vn', '-ac', '1', '-ar', str(sample_rate), '-f', 's16le', '-c:a', 'pcm_s16le', '-']
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
        count = 0
        try:
            while chunk := process.stdout.read(1024 * 1024):
                count += len(chunk)
            if process.wait(timeout=30):
                raise RuntimeError('FFmpeg could not decode a validation artifact')
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    return count / (sample_rate * 2)


@contextmanager
def isolated_server(library_root):
    environment = {name: value for name, value in os.environ.items() if not name.startswith('CASTWELL_')}
    environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                       CASTWELL_MODEL_CACHE=str(library_root.parent / 'model-cache'),
                       HF_XET_CACHE=str(library_root.parent / 'model-cache' / '.xet'))
    with patch.dict(os.environ, environment, clear=True), \
         patch('castwell.processing.transcribe', side_effect=AssertionError('This gate must not run a transcription model')) as transcribe, \
         patch('castwell.processing.detect_ads', side_effect=AssertionError('This gate must not run an ad detector')) as detector:
        app = create_app(library_root)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        address = f'http://127.0.0.1:{listener.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, name='castwell-long-smoke', daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError('The isolated workflow server could not start')
                time.sleep(.05)
            with requests.Session() as client:
                client.trust_env = False
                yield app, client, address, transcribe, detector
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()
            if thread.is_alive():
                raise RuntimeError('The isolated workflow server did not shut down')


def request(client, base, method, route, **kwargs):
    response = client.request(method, base + route, timeout=(5, 30), **kwargs)
    if not response.ok:
        raise RuntimeError(f'Workflow route {route} failed with HTTP {response.status_code}')
    return response


def wait_for_jobs(client, base, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        busy = bool(request(client, base, 'GET', '/api/jobs').json()['jobs'])
        episodes = request(client, base, 'GET', '/api/episodes').json()['episodes']
        if any(episode['status'] == 'error' for episode in episodes):
            raise RuntimeError('An isolated episode processing job failed')
        if not busy:
            return {episode['id']: episode for episode in episodes}
        time.sleep(.2)
    raise RuntimeError('The isolated audio pipeline exceeded its ten-minute limit')


def run_workflow(duration_seconds=3600):
    if not math.isfinite(duration_seconds) or not 120 <= duration_seconds <= 14400:
        raise ValueError('Choose a duration from 120 to 14400 seconds')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise RuntimeError('FFmpeg and FFprobe must be installed for this workflow gate')
    started = time.monotonic()
    report = {'format': 'castwell-long-audio-workflow', 'version': 1,
              'created_at': datetime.now(timezone.utc).isoformat(),
              'fixture': 'Generated 440 Hz mono tone with supplied transcript and manual cut boundaries',
              'tests_ad_detection_accuracy': False, 'duration_seconds': float(duration_seconds),
              'stages': {}, 'inference_calls': 0}
    with tempfile.TemporaryDirectory(prefix='castwell-long-workflow-') as directory:
        workspace = Path(directory)
        # A source, two originals, edited MP3, archive and extracted ZIP members
        # require less than 256000 bytes per fixture second at these encodings.
        required_storage = int(duration_seconds * 256000) + 32 * 1024**2
        if shutil.disk_usage(workspace).free < required_storage:
            raise RuntimeError('Not enough temporary disk space for the full-length workflow gate')
        source = workspace / 'tone.wav'
        phase = time.monotonic()
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                        '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=16000:duration={duration_seconds}',
                        '-c:a', 'pcm_s16le', str(source)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        report['stages']['generate_seconds'] = round(time.monotonic() - phase, 3)
        source_hash = digest(source)
        library_root = workspace / 'library'
        library = Library(library_root)
        ids = ['long-keep', 'long-remove']
        library.import_entries([{'id': episode_id, 'title': f'{duration_seconds / 60:g}-minute {mode} fixture',
                                 'podcast': f'Long {mode} fixture', 'description': 'Generated audio workflow fixture',
                                 'image': '', 'published': '2026-10-11', 'duration': duration_seconds,
                                 'feed_url': f'https://fixture.invalid/{mode}.xml', 'media_url': ''}
                                for episode_id, mode in zip(ids, ('keep', 'remove'))])
        for episode_id in ids:
            original = library.directory(episode_id) / 'original.wav'
            shutil.copyfile(source, original)
            library.update(episode_id, audio=str(original), duration=duration_seconds, status='downloaded')
        report['source_bytes'] = source.stat().st_size
        cut_start = float(duration_seconds / 6)
        cut_seconds = float(min(60, duration_seconds / 6))
        cut_end = cut_start + cut_seconds
        transcript = {'language': 'en', 'duration': duration_seconds, 'segments': [
            {'start': 0, 'end': cut_start, 'text': 'Fixture programme opening.'},
            {'start': cut_start, 'end': cut_end, 'text': 'Manually labelled fixture advertisement.'},
            {'start': cut_end, 'end': duration_seconds, 'text': 'Fixture programme continues to its conclusion.'},
        ]}
        initial_cut = {'start': cut_start, 'end': cut_end, 'approved': False,
                       'reason': 'Known fixture boundary, pending listener approval', 'source': 'manual'}
        with isolated_server(library_root) as (app, client, base, transcribe, detector):
            keep_show = app.state.library.get(ids[0])['podcast_id']
            request(client, base, 'PATCH', f'/api/podcasts/{keep_show}', json={'ad_mode': 'keep'})
            for episode_id in ids:
                request(client, base, 'POST', f'/api/episodes/{episode_id}/transcript', json=transcript)
                request(client, base, 'POST', f'/api/episodes/{episode_id}/cuts', json={'cuts': [initial_cut]})
            phase = time.monotonic()
            queued = request(client, base, 'POST', '/api/process', json={'ids': ids}).json()
            assert queued['queued'] == 2 and queued['failed'] == 0
            choices = {item['episode_id']: item['remove_ads'] for item in queued['results']}
            assert choices == {'long-keep': False, 'long-remove': True}
            states = wait_for_jobs(client, base)
            assert states[ids[0]]['status'] == 'ready' and not states[ids[0]]['has_cleaned']
            assert states[ids[1]]['status'] == 'review' and not states[ids[1]]['has_cleaned']
            report['stages']['mixed_prepare_seconds'] = round(time.monotonic() - phase, 3)
            request(client, base, 'POST', f'/api/episodes/{ids[1]}/cuts',
                    json={'cuts': [{**initial_cut, 'approved': True, 'reason': 'Known boundary approved by fixture listener'}]})
            phase = time.monotonic()
            rendered = request(client, base, 'POST', '/api/render', json={'ids': ids}).json()
            assert rendered['queued'] == 1 and rendered['skipped'] == 1 and rendered['failed'] == 0
            states = wait_for_jobs(client, base)
            assert states[ids[1]]['status'] == 'ready' and states[ids[1]]['has_cleaned']
            report['stages']['render_seconds'] = round(time.monotonic() - phase, 3)
            archive_path = workspace / 'selected.zip'
            phase = time.monotonic()
            with request(client, base, 'POST', '/api/export/archive/form',
                         data={'payload': json.dumps({'ids': ids, 'include_transcripts': True})}, stream=True) as response:
                assert response.headers['X-Castwell-Included'] == '2'
                assert response.headers['X-Castwell-Omitted'] == '0'
                with archive_path.open('wb') as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        output.write(chunk)
            report['stages']['archive_seconds'] = round(time.monotonic() - phase, 3)
            report['archive_bytes'] = archive_path.stat().st_size
            report['episodes'] = []
            phase = time.monotonic()
            with zipfile.ZipFile(archive_path) as archive:
                manifest = json.loads(archive.read('report.json'))
                assert manifest['included_count'] == 2 and manifest['omitted_count'] == 0
                for episode_id in ids:
                    record = app.state.library.get(episode_id)
                    original_hash = digest(record['audio'])
                    assert original_hash == source_hash
                    item = next(item for item in manifest['included'] if item['episode_id'] == episode_id)
                    expected_variant = 'original' if episode_id == ids[0] else 'cleaned'
                    expected_duration = float(duration_seconds if episode_id == ids[0] else duration_seconds - cut_seconds)
                    assert item['version'] == expected_variant
                    exported = workspace / (episode_id + Path(item['file']).suffix)
                    # Avoid extractall and keep the exported audio copy bounded.
                    with archive.open(item['file']) as source_stream, exported.open('wb') as output:
                        shutil.copyfileobj(source_stream, output, length=1024 * 1024)
                    if expected_variant == 'original':
                        assert digest(exported) == source_hash
                    decoded = decoded_seconds(exported)
                    assert abs(decoded - expected_duration) <= .1
                    assert abs(record['removed_seconds'] - (cut_seconds if expected_variant == 'cleaned' else 0)) <= .1
                    exported_transcript = json.loads(archive.read(item['transcript_file']))
                    assert abs(exported_transcript['duration'] - expected_duration) <= .1
                    report['episodes'].append({'id': episode_id, 'ad_mode': record['ad_mode'], 'exported_version': item['version'],
                                               'original_preserved': original_hash == source_hash,
                                               'decoded_seconds': decoded, 'expected_seconds': expected_duration,
                                               'removed_seconds': record['removed_seconds'], 'export_bytes': exported.stat().st_size,
                                               'waveform_bins': len(record['waveform']['peaks'])})
            report['stages']['verify_seconds'] = round(time.monotonic() - phase, 3)
            assert transcribe.call_count == 0 and detector.call_count == 0
            report['inference_calls'] = transcribe.call_count + detector.call_count
            # Response cleanup can finish just after the HTTP reader sees EOF.
            deadline = time.monotonic() + 3
            while list((library_root / '.exports').iterdir()) and time.monotonic() < deadline:
                time.sleep(.02)
            assert list((library_root / '.exports').iterdir()) == []
            report['archive_workspace_cleaned'] = True
        report['temporary_storage_bytes_before_cleanup'] = sum(path.stat().st_size for path in workspace.rglob('*') if path.is_file())
    report.update(passed=True, temporary_library_cleaned=True, elapsed_seconds=round(time.monotonic() - started, 3))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration-seconds', type=float, default=3600, help='Fixture duration; default 3600 seconds')
    parser.add_argument('--output', type=Path, help='Save a JSON report after temporary audio is cleaned')
    args = parser.parse_args()
    try:
        report = run_workflow(args.duration_seconds)
    except Exception as exc:
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        print(f'Long episode workflow failed at {Path(frame.filename).name}:{frame.lineno}: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    body = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(body, encoding='utf-8')
    print(body, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
