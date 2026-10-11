"""Local gallery and a serialized, resumable audio processing queue."""
import json
import hashlib
import re
import tempfile
import shutil
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
import mimetypes
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, ConfigDict

from . import processing, __version__
from .feeds import read_feed
from .library import ACTIVE_STATUSES, Library
from .jobs import Jobs, SerialWorker
from .config import Settings, warm_transcription_model
from .middleware import AllowedHosts, RequestSizeLimit


MAX_ARCHIVE_AUDIO_BYTES = 10 * 1024**3


class FeedRequest(BaseModel):
    url: str = Field(min_length=1, max_length=8192)


class ProcessRequest(BaseModel):
    remove_ads: bool | None = Field(default=None, strict=True)
    detector: str | None = None
    redetect: bool = False
    retranscribe: bool = False
    download_only: bool = False


class BatchRequest(ProcessRequest):
    ids: list[str] = Field(min_length=1, max_length=500)


class IdsRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


class PodcastPreference(BaseModel):
    ad_mode: str


class BatchPodcastPreference(PodcastPreference, IdsRequest):
    pass


class ArchiveRequest(IdsRequest):
    version: str = 'preferred'
    include_transcripts: bool = Field(default=False, strict=True)


class BatchEpisodeState(IdsRequest):
    model_config = ConfigDict(extra='forbid')
    favorite: bool | None = Field(default=None, strict=True)
    archived: bool | None = Field(default=None, strict=True)
    played: bool | None = Field(default=None, strict=True)


class ManagedArchiveResponse(FileResponse):
    """Release owned archive snapshots even if a client disconnects mid-transfer."""

    def __init__(self, *args, cleanup, **kwargs):
        super().__init__(*args, **kwargs)
        self._cleanup = cleanup
        self._cleanup_lock = threading.Lock()
        self._released = False

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with self._cleanup_lock:
                if not self._released:
                    self._released = True
                    self._cleanup()


class CutsRequest(BaseModel):
    cuts: list[dict] = Field(max_length=10000)



class EpisodeState(BaseModel):
    favorite: bool | None = None
    archived: bool | None = None
    played: bool | None = None
    position: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class OpmlRequest(BaseModel):
    xml: str = Field(min_length=1, max_length=2 * 1024 * 1024)


class ModelPreparation:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.pool = SerialWorker(name='castwell-model')
        self.closed = False
        self.state = {'status': 'idle', 'progress': '', 'error': None}

    def get(self):
        with self.lock:
            return dict(self.state)

    def submit(self):
        selected = self.settings.get()
        with self.lock:
            if self.closed or self.state['status'] == 'running':
                return False
            self.state = {'status': 'running', 'progress': 'Preparing the selected speech model', 'error': None}
            try:
                self.pool.submit(self.run, selected)
            except RuntimeError:
                self.state = {'status': 'error', 'progress': '', 'error': 'The model preparation worker is unavailable'}
                return False
            return True

    def run(self, selected):
        def progress(message):
            with self.lock:
                self.state['progress'] = str(message)
        try:
            warm_transcription_model(selected, progress=progress)
            with self.lock:
                self.state = {'status': 'ready', 'progress': f"Speech model {selected['transcription_model']} is ready", 'error': None}
        except Exception:
            with self.lock:
                self.state = {'status': 'error', 'progress': '', 'error': 'Could not prepare the speech model. Check model name, network access, and available disk space.'}

    def shutdown(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)


def _timestamp_now():
    return datetime.now(timezone.utc).isoformat()


def _opml_urls(xml):
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', xml, re.I):
        raise ValueError('OPML must not contain document types or entity declarations')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ValueError('This is not a valid OPML file') from None
    if root.tag.lower() != 'opml':
        raise ValueError('Expected an OPML subscription file')
    urls = list(dict.fromkeys(node.attrib['xmlUrl'].strip() for node in root.iter('outline') if node.attrib.get('xmlUrl', '').strip()))
    if len(urls) > 100:
        raise ValueError('Import up to 100 subscriptions at a time')
    return urls


def create_app(data_dir=None):
    library = Library(data_dir or os.getenv('CASTWELL_DATA_DIR', 'data'))
    model = os.getenv('CASTWELL_TRANSCRIPTION_MODEL', 'base')
    settings = Settings(library)
    # Hugging Face's transfer cache is separate from download_root. Choose a
    # writable location before a diagnostics/model call imports its constants.
    os.environ.setdefault('HF_XET_CACHE', str(Path(settings.get()['model_cache']) / '.xet'))
    jobs = Jobs(library, model, settings_getter=settings.get)
    model_work = ModelPreparation(settings)
    started_at = _timestamp_now()
    feed_refresh_pool = SerialWorker(name='castwell-feeds')
    feed_refresh_lock = threading.Lock()
    feed_refresh_state = {'status': 'idle', 'results': [], 'total': 0, 'completed': 0}
    export_slots = threading.BoundedSemaphore(2)

    @asynccontextmanager
    async def lifespan(app):
        library.recover()
        yield
        jobs.shutdown()
        model_work.shutdown()
        feed_refresh_pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title='Castwell', lifespan=lifespan)
    app.add_middleware(RequestSizeLimit)
    app.add_middleware(AllowedHosts)
    app.state.library, app.state.jobs, app.state.settings = library, jobs, settings

    @app.middleware('http')
    async def local_requests(request: Request, call_next):
        # A local service must not accept mutations from arbitrary web pages.
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail': 'Cross-origin changes are not allowed'}, status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': 'Cross-site changes are not allowed'}, status_code=403)
            limit = 2 * 1024**3 + 1024**2 if request.url.path == '/api/audio' else 25 * 1024**2
            try:
                if int(request.headers.get('content-length', '0')) > limit:
                    return JSONResponse({'detail': 'The upload exceeds the allowed size'}, status_code=413)
            except ValueError:
                return JSONResponse({'detail': 'Invalid content length'}, status_code=400)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Chromium gives native POST forms Origin:null under no-referrer.
        # Same-origin retains useful form provenance while sending no referrer
        # to external podcast artwork or other external destinations.
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: http: data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({'detail': 'Episode not found'}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Pydantic's default response includes raw inputs, including premium URLs.
        messages = [error['msg'] for error in exc.errors()]
        return JSONResponse({'detail': '; '.join(messages)}, status_code=422)

    @app.exception_handler(processing.ProcessingError)
    async def processing_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    def ensure_idle(episode_id):
        library.get(episode_id)
        if episode_id in jobs.pending:
            raise HTTPException(409, 'This episode is processing. Wait for it to finish.')

    def batch_response(results):
        response = {name: sum(item['status'] == name for item in results)
                    for name in ('queued', 'skipped', 'failed', 'updated', 'cancelled')}
        response['results'] = results
        return response

    def selected_records(ids):
        records, results = [], []
        for episode_id in dict.fromkeys(ids):
            try:
                records.append(library.get(episode_id, include_large=False))
            except KeyError:
                results.append({'episode_id': episode_id, 'status': 'failed', 'detail': 'Episode not found'})
        return records, results

    @app.get('/api/health')
    def health():
        snapshot = jobs.snapshot()
        with feed_refresh_lock:
            refresh = {key: feed_refresh_state[key] for key in ('status', 'total', 'completed')}
        return {'status': 'ok', 'version': __version__, 'startup_time': started_at,
                'refresh': refresh, 'refresh_running': refresh['status'] == 'running',
                'queue': {'total': len(snapshot), 'queued': sum(item['status'] == 'queued' for item in snapshot),
                          'active': sum(item['status'] != 'queued' for item in snapshot)}}

    @app.get('/api/podcasts')
    def podcasts():
        return {'podcasts': library.podcasts()}

    @app.patch('/api/podcasts')
    def batch_podcast_preference(body: BatchPodcastPreference):
        if body.ad_mode not in ('remove', 'keep'):
            raise ValueError('Ad mode must be remove or keep.')
        existing = {record['id'] for record in library.podcasts()}
        valid, results = [], []
        for podcast_id in dict.fromkeys(body.ids):
            if podcast_id in existing:
                valid.append(podcast_id)
            else:
                results.append({'podcast_id': podcast_id, 'status': 'failed', 'detail': 'Podcast not found'})
        library.set_podcast_mode(valid, body.ad_mode)
        results.extend({'podcast_id': podcast_id, 'status': 'updated', 'ad_mode': body.ad_mode} for podcast_id in valid)
        return batch_response(results)

    @app.patch('/api/podcasts/{podcast_id}')
    def podcast_preference(podcast_id: str, body: PodcastPreference):
        library.set_podcast_mode([podcast_id], body.ad_mode)
        return next((record for record in library.podcasts() if record['id'] == podcast_id), {'id': podcast_id, 'ad_mode': body.ad_mode})

    @app.get('/api/episodes')
    def episodes(request: Request):
        records = library.summaries()
        payload = {'episodes': records, 'settings': settings.get(), 'stats': {
            'episodes': len(records), 'ready': sum(item['status'] == 'ready' for item in records),
            'processing': sum(item['status'] in ACTIVE_STATUSES for item in records),
            'removed_seconds': sum(item['removed_seconds'] for item in records),
        }}
        response = JSONResponse(payload)
        etag = '"' + hashlib.sha256(response.body).hexdigest() + '"'
        headers = {'ETag': etag, 'Cache-Control': 'private, no-cache'}
        matches = request.headers.get('if-none-match', '').split(',')
        if any(candidate.strip() == '*' or candidate.strip().removeprefix('W/') == etag for candidate in matches):
            return Response(status_code=304, headers=headers)
        response.headers.update(headers)
        return response

    @app.get('/api/episodes/{episode_id}')
    def episode(episode_id: str):
        return library.public(library.get(episode_id), detail=True)

    @app.post('/api/feeds')
    def add_feed(body: FeedRequest):
        entries = read_feed(body.url)
        feed = library.add_feed(body.url, title=entries[0]['podcast'] if entries else urlsplit(body.url).hostname or 'Podcast', image=entries[0].get('image', '') if entries else '')
        library.mark_feed(feed['id'], last_checked=_timestamp_now(), error=None)
        return {'added': library.import_entries(entries), 'total': len(entries)}

    @app.get('/api/feeds')
    def feeds():
        return {'feeds': library.feeds()}

    @app.post('/api/feeds/refresh', status_code=202)
    def refresh_subscriptions():
        """Refresh a whole subscription library without holding an HTTP request open."""
        feeds_to_refresh = library.feeds(public=False)
        with feed_refresh_lock:
            if feed_refresh_state['status'] == 'running':
                return {'started': False, 'total': feed_refresh_state['total']}
            feed_refresh_state.update(status='running', results=[], total=len(feeds_to_refresh), completed=0)

        def run_refresh():
            for feed in feeds_to_refresh:
                try:
                    # Removed subscriptions are not recreated by a queued refresh.
                    current = library.get_feed(feed['id'])
                    entries = read_feed(current['url'])
                    with jobs.lock:
                        library.get_feed(feed['id'])
                        values = {'last_checked': _timestamp_now(), 'error': None}
                        if entries:
                            values.update(title=entries[0]['podcast'], image=entries[0].get('image', ''))
                        library.mark_feed(feed['id'], **values)
                        result = {'feed_id': feed['id'], 'status': 'updated', 'added': library.import_entries(entries), 'total': len(entries)}
                except KeyError:
                    result = {'feed_id': feed['id'], 'status': 'skipped', 'detail': 'Subscription was removed'}
                except Exception:
                    result = {'feed_id': feed['id'], 'status': 'failed', 'detail': 'Could not refresh this subscription. Check its address and access credentials.'}
                    try:
                        library.mark_feed(feed['id'], last_checked=_timestamp_now(), error=result['detail'])
                    except KeyError:
                        pass
                with feed_refresh_lock:
                    feed_refresh_state['results'].append(result)
                    feed_refresh_state['completed'] += 1
            with feed_refresh_lock:
                feed_refresh_state['status'] = 'complete'
        try:
            feed_refresh_pool.submit(run_refresh)
        except RuntimeError:
            with feed_refresh_lock:
                feed_refresh_state.update(status='error')
            raise HTTPException(503, 'The subscription worker is unavailable') from None
        return {'started': True, 'total': len(feeds_to_refresh)}

    @app.get('/api/feeds/refresh/status')
    def refresh_subscriptions_status():
        with feed_refresh_lock:
            return {**feed_refresh_state, 'results': [dict(item) for item in feed_refresh_state['results']]}

    @app.post('/api/feeds/opml')
    def import_opml(body: OpmlRequest):
        added, imported, errors = 0, 0, []
        for url in _opml_urls(body.xml):
            try:
                result = add_feed(FeedRequest(url=url))
                added += result['added']
                imported += 1
            except ValueError as exc:
                try:
                    title = urlsplit(url).hostname or 'Subscription'
                except ValueError:
                    title = 'Subscription'
                errors.append({'title': title, 'error': 'Enter a valid feed URL of at most 8192 characters' if isinstance(exc, ValidationError) else str(exc)})
        return {'imported': imported, 'added': added, 'errors': errors}

    @app.get('/api/feeds/export')
    def export_opml():
        root = ET.Element('opml', version='2.0')
        ET.SubElement(ET.SubElement(root, 'head'), 'title').text = 'Castwell subscriptions'
        body = ET.SubElement(root, 'body')
        skipped = 0
        for feed in library.feeds(public=False):
            parsed = urlsplit(feed['url'])
            if parsed.username or parsed.password or parsed.query:
                skipped += 1
                continue
            ET.SubElement(body, 'outline', type='rss', text=feed['title'], title=feed['title'], xmlUrl=feed['url'])
        return Response(ET.tostring(root, encoding='utf-8', xml_declaration=True), media_type='text/x-opml', headers={'Content-Disposition': 'attachment; filename="castwell-subscriptions.opml"', 'X-Castwell-Private-Feeds-Omitted': str(skipped)})

    @app.post('/api/feeds/{feed_id}/refresh')
    def refresh_feed(feed_id: str):
        feed = library.get_feed(feed_id)
        try:
            entries = read_feed(feed['url'])
            with jobs.lock:
                # Fetch outside the lock, then respect an unsubscribe that
                # completed while the network request was in progress.
                library.get_feed(feed_id)
                values = {'last_checked': _timestamp_now(), 'error': None}
                if entries:
                    values.update(title=entries[0]['podcast'], image=entries[0].get('image', ''))
                library.mark_feed(feed_id, **values)
                return {'added': library.import_entries(entries), 'total': len(entries)}
        except ValueError as exc:
            with jobs.lock:
                library.mark_feed(feed_id, last_checked=_timestamp_now(), error=str(exc))
            raise

    @app.delete('/api/feeds/{feed_id}')
    def unsubscribe(feed_id: str):
        with jobs.lock:
            library.delete_feed(feed_id)
        return {'unsubscribed': True}

    @app.post('/api/audio', status_code=201)
    def import_audio(file: UploadFile = File(...)):
        extension = Path(file.filename or '').suffix.lower()
        if extension not in ('.mp3', '.wav', '.m4a', '.mp4', '.flac', '.ogg', '.opus', '.aac'):
            raise ValueError('Choose an MP3, WAV, M4A, MP4, FLAC, OGG, Opus, or AAC recording')
        upload_dir = library.root / '.uploads'
        upload_dir.mkdir(exist_ok=True)
        name = re.sub(r'[\x00-\x1f\x7f]', '', Path((file.filename or 'Recording').replace('\\', '/')).stem)[:200] or 'Recording'
        temporary = None
        try:
            # Keep existing upload IDs stable when reopening a library.
            digest = hashlib.sha256(b'podgrab-local-audio\0')
            received = 0
            with tempfile.NamedTemporaryFile(dir=upload_dir, suffix=extension, delete=False) as stream:
                temporary = Path(stream.name)
                while chunk := file.file.read(1024 * 1024):
                    received += len(chunk)
                    if received > 2 * 1024**3:
                        raise ValueError('Audio uploads are limited to 2 GB')
                    stream.write(chunk)
                    digest.update(chunk)
            if not received:
                raise ValueError('The audio file is empty')
            duration = processing.probe_duration(temporary)
            episode_id = digest.hexdigest()[:24]
            with jobs.lock:
                if episode_id in jobs.pending:
                    raise HTTPException(409, 'This recording is already processing')
                try:
                    existing = library.get(episode_id)
                    if existing['audio'] and Path(existing['audio']).is_file():
                        return library.public(existing, detail=True)
                except KeyError:
                    library.import_entries([{'id': episode_id, 'title': name, 'podcast': 'Local uploads', 'description': 'An audio recording imported from your device.', 'image': '', 'published': _timestamp_now(), 'feed_url': '', 'media_url': '', 'source': 'local', 'duration': duration}])
                output = library.directory(episode_id) / ('original' + extension)
                os.replace(temporary, output)
                library.update(episode_id, audio=str(output), duration=duration, status='downloaded', progress='Audio imported. Prepare this episode to create its transcript.', error=None)
                return library.public(library.get(episode_id), detail=True)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            file.file.close()

    @app.patch('/api/episodes/batch/state')
    def batch_episode_state(body: BatchEpisodeState):
        values = body.model_dump(exclude={'ids'}, exclude_none=True)
        if not values:
            raise ValueError('Choose favorite, archived, or played for a batch update.')
        records, results = selected_records(body.ids)
        ids = [record['id'] for record in records]
        library.batch_episode_flags(ids, **values)
        results.extend({'episode_id': episode_id, 'status': 'updated'} for episode_id in ids)
        return batch_response(results)

    @app.patch('/api/episodes/{episode_id}/state')
    def episode_state(episode_id: str, body: EpisodeState):
        record = library.get(episode_id)
        values = body.model_dump(exclude_none=True)
        if 'position' in values:
            values['position'] = min(values['position'], record['duration'])
        if 'position' in values or values.get('played'):
            values['last_played'] = _timestamp_now()
        if values:
            library.update(episode_id, **values)
        return library.public(library.get(episode_id))

    @app.get('/api/episodes/{episode_id}/waveform')
    def waveform(episode_id: str):
        record = library.get(episode_id)
        if record.get('waveform'):
            return record['waveform']
        if not record['audio']:
            raise HTTPException(404, 'Download audio to see its waveform')
        with jobs.lock:
            if episode_id in jobs.pending:
                return {'duration': record['duration'], 'peaks': []}
            result = processing.waveform(Path(record['audio']))
            library.update(episode_id, waveform=result)
            return result

    @app.get('/api/episodes/{episode_id}/revisions')
    def revisions(episode_id: str):
        library.get(episode_id)
        return {'revisions': library.revisions(episode_id)}

    @app.post('/api/episodes/{episode_id}/revisions/{revision_id}/restore')
    def restore_revision(episode_id: str, revision_id: str):
        with jobs.lock:
            ensure_idle(episode_id)
            record = library.get(episode_id)
            cuts = processing.validate_cuts(library.revision(episode_id, revision_id)['cuts'], record['duration'])
            library.save_revision(episode_id, record['cuts'], 'Before restoring an earlier edit')
            library.update(episode_id, cuts=cuts, cleaned=None, cleaned_duration=0, removed_seconds=0, analysis_done=True, pending_job=None, status='review', error=None, progress='Earlier cuts restored. Export to apply them.')
            return library.public(library.get(episode_id), detail=True)

    @app.get('/api/episodes/{episode_id}/decisions')
    def export_decisions(episode_id: str):
        record = library.get(episode_id)
        body = {'format': 'castwell-decisions', 'version': 1, 'episode': {'id': episode_id, 'title': record['title'], 'podcast': record['podcast'], 'duration': record['duration']}, 'timeline': 'original', 'cuts': record['cuts'], 'transcript': record['transcript']}
        return Response(json.dumps(body, indent=2, ensure_ascii=False), media_type='application/json', headers={'Content-Disposition': f'attachment; filename="{episode_id}-decisions.json"'})

    @app.get('/api/jobs')
    def queue():
        return {'jobs': jobs.snapshot()}

    @app.post('/api/episodes/batch/cancel')
    def batch_cancel(body: IdsRequest):
        with jobs.lock:
            records, results = selected_records(body.ids)
            for record in records:
                cancelled = jobs.cancel(record['id'])
                results.append({'episode_id': record['id'], 'status': 'cancelled' if cancelled else 'skipped',
                                'detail': 'Cancellation requested' if cancelled else 'Episode is not processing'})
        return batch_response(results)

    @app.post('/api/episodes/{episode_id}/cancel')
    def cancel(episode_id: str):
        library.get(episode_id)
        return {'cancelled': jobs.cancel(episode_id)}

    @app.get('/api/settings')
    def get_settings():
        return settings.get()

    @app.patch('/api/settings')
    def update_settings(body: dict):
        return settings.update(body)

    @app.get('/api/diagnostics')
    def diagnostics():
        return settings.diagnostics()

    @app.post('/api/settings/test-classifier')
    def test_classifier():
        return settings.test_classifier()

    @app.post('/api/models/prepare', status_code=202)
    def prepare_model():
        return {'started': model_work.submit()}

    @app.get('/api/models/status')
    def model_status():
        return model_work.get()

    def check_detector(detector):
        if detector not in (None, 'auto', 'heuristic', 'ai'):
            raise ValueError('Detector must be auto, heuristic, or ai')

    def check_process_options(body):
        check_detector(body.detector)
        if body.download_only and (body.redetect or body.retranscribe):
            raise ValueError('Audio-only downloads cannot also request transcription or ad detection')

    def queue_selection(ids, operation, options=None, retry=False):
        with jobs.lock:
            records, results = selected_records(ids)
            for record in records:
                episode_id = record['id']
                detail = None
                if episode_id in jobs.pending:
                    detail = 'Episode is already processing'
                elif retry and record['status'] not in ('error', 'cancelled'):
                    detail = 'Only failed or cancelled episodes need a retry'
                elif operation == 'download' and record['audio'] and Path(record['audio']).is_file():
                    detail = 'Original audio is already downloaded'
                elif operation == 'render':
                    if record.get('ad_mode') == 'keep':
                        detail = 'Podcast keeps ads; the original audio is used'
                    elif not record['audio'] or not Path(record['audio']).is_file():
                        results.append({'episode_id': episode_id, 'status': 'failed', 'detail': 'Download original audio before exporting cuts'})
                        continue
                    elif not record.get('analysis_done') and not record['cuts']:
                        detail = 'Prepare and review this episode before exporting cuts'
                    elif record['cuts'] and not any(cut.get('approved') for cut in record['cuts']):
                        detail = 'Approve the proposed cuts before exporting'
                if detail:
                    results.append({'episode_id': episode_id, 'status': 'skipped', 'detail': detail})
                    continue
                try:
                    queued = jobs.submit(episode_id, operation, options)
                except Exception:
                    results.append({'episode_id': episode_id, 'status': 'failed', 'detail': 'Could not queue this episode. Completed work is preserved.'})
                    continue
                request = library.get(episode_id, include_large=False).get('pending_job') or {}
                remove_ads = request.get('options', {}).get('remove_ads', record.get('ad_mode') == 'remove')
                results.append({'episode_id': episode_id, 'status': 'queued' if queued else 'skipped',
                                'remove_ads': remove_ads, 'ad_mode': 'remove' if remove_ads else 'keep'})
        return batch_response(results)

    @app.post('/api/episodes/{episode_id}/process', status_code=202)
    def process(episode_id: str, body: ProcessRequest):
        check_process_options(body)
        return {'queued': jobs.submit(episode_id, 'process', body)}

    @app.post('/api/process', status_code=202)
    def batch(body: BatchRequest):
        check_process_options(body)
        return queue_selection(body.ids, 'process', body)

    @app.post('/api/download', status_code=202)
    def batch_download(body: IdsRequest):
        return queue_selection(body.ids, 'download')

    @app.post('/api/render', status_code=202)
    def batch_render(body: IdsRequest):
        return queue_selection(body.ids, 'render')

    @app.post('/api/episodes/batch/retry', status_code=202)
    def batch_retry(body: IdsRequest):
        return queue_selection(body.ids, 'process', retry=True)

    @app.post('/api/episodes/{episode_id}/cuts')
    def save_cuts(episode_id: str, body: CutsRequest):
        with jobs.lock:
            ensure_idle(episode_id)
            record = library.get(episode_id)
            if record['duration'] <= 0:
                raise ValueError('Download this episode before adding cuts')
            cuts = processing.validate_cuts(body.cuts, record['duration'])
            if cuts != record['cuts']:
                library.save_revision(episode_id, record['cuts'], 'Before manual cut changes')
            library.update(episode_id, cuts=cuts, cleaned=None, cleaned_duration=0, removed_seconds=0, analysis_done=True, pending_job=None, status='review', error=None, progress='Cut changes saved. Export to apply them.')
            return library.public(library.get(episode_id), detail=True)

    @app.post('/api/episodes/{episode_id}/render', status_code=202)
    def render(episode_id: str):
        record = library.get(episode_id)
        if not record['audio']:
            raise ValueError('Process the episode to download audio first')
        if not record.get('analysis_done') and not record['cuts']:
            raise ValueError('Prepare and review this episode before exporting cuts')
        return {'queued': jobs.submit(episode_id, 'render')}

    @app.post('/api/episodes/{episode_id}/transcript')
    def import_transcript(episode_id: str, body: dict):
        with jobs.lock:
            ensure_idle(episode_id)
            record = library.get(episode_id)
            transcript = processing.validate_transcript(body, record['duration'] or None)
            if record['cuts']:
                library.save_revision(episode_id, record['cuts'], 'Before replacing the transcript')
            library.update(episode_id, transcript=transcript, cuts=[], cleaned=None, cleaned_duration=0, removed_seconds=0, analysis_done=False, pending_job=None, error=None, status='available', progress='Transcript imported. Process to detect ads.')
            return library.public(library.get(episode_id), detail=True)

    @app.get('/api/episodes/{episode_id}/transcript')
    def export_transcript(episode_id: str, format: str = 'txt', version: str = 'original'):
        record = library.get(episode_id)
        if not record['transcript']:
            raise HTTPException(404, 'No transcript yet')
        if format not in ('txt', 'srt', 'vtt', 'json') or version not in ('original', 'cleaned'):
            raise ValueError('Unsupported transcript format or version')
        transcript = record['transcript']
        if version == 'cleaned':
            if not record['cleaned']:
                raise ValueError('Export approved cuts before downloading a cleaned transcript')
            transcript = processing.cleaned_transcript(transcript, record['cuts'])
        content = json.dumps(transcript, indent=2, ensure_ascii=False) if format == 'json' else processing.transcript_text(transcript, format)
        return Response(content, media_type='application/json' if format == 'json' else 'text/plain', headers={'Content-Disposition': f'attachment; filename="{episode_id}-{version}.{format}"'})

    @app.get('/media/{episode_id}/{version}')
    def media(episode_id: str, version: str):
        if version not in ('original', 'cleaned'):
            raise HTTPException(404, 'Audio version not found')
        record = library.get(episode_id)
        path = record['audio' if version == 'original' else 'cleaned']
        if not path or not Path(path).is_file():
            raise HTTPException(404, 'Audio is not available yet')
        name = re.sub(r'[^\w .-]', '', record['title']).strip()[:100] or episode_id
        return FileResponse(path, media_type=mimetypes.guess_type(path)[0] or 'audio/mpeg', filename=f"{name}-{version}{Path(path).suffix}", content_disposition_type='inline')

    def build_archive(body):
        """Disk-backed export snapshots published audio without loading it into RAM."""
        if body.version not in ('preferred', 'original', 'cleaned'):
            raise ValueError('Archive version must be preferred, original, or cleaned')
        if not export_slots.acquire(blocking=False):
            raise HTTPException(429, 'Two archive downloads are already active. Retry after a download finishes.')
        maximum = MAX_ARCHIVE_AUDIO_BYTES
        export_root = library.root / '.exports'
        try:
            export_root.mkdir(exist_ok=True)
            workspace = tempfile.TemporaryDirectory(prefix='archive-', dir=export_root)
        except OSError:
            export_slots.release()
            raise ValueError('The archive workspace is unavailable. Check library disk space and permissions.') from None
        temporary = Path(workspace.name)
        report = {'format': 'castwell-batch-export', 'version': 1, 'created_at': _timestamp_now(),
                  'requested_version': body.version, 'included': [], 'omitted': [],
                  'maximum_audio_bytes': maximum, 'maximum_payload_bytes': maximum,
                  'review_policy': 'Only listener-approved cuts are present in edited audio.'}
        snapshots, total_bytes, transcript_bytes = [], 0, 0
        try:
            with jobs.lock:
                records, failures = selected_records(body.ids)
                report['omitted'].extend(failures)
                for index, record in enumerate(records):
                    info = {'episode_id': record['id'], 'title': record.get('title', 'Episode'),
                            'podcast': record.get('podcast', 'Podcast'), 'ad_mode': record.get('ad_mode', 'remove')}
                    reason = None
                    public = library.public(record)
                    variant = public['preferred_version'] if body.version == 'preferred' else body.version
                    if record['id'] in jobs.pending:
                        reason = 'Episode is processing; export after it completes'
                    elif body.version == 'preferred' and not public['export_ready']:
                        reason = 'Prepare the episode and review its proposed cuts before exporting'
                    elif variant == 'cleaned' and not any(cut.get('approved') is True for cut in record['cuts']):
                        reason = 'No approved edited audio is available'
                    if not reason and variant == 'cleaned':
                        try:
                            processing.validate_cuts(record['cuts'], record['duration'])
                        except (ValueError, TypeError, KeyError):
                            reason = 'Saved cuts are invalid; review them before exporting edited audio'
                    path_value = record['cleaned' if variant == 'cleaned' else 'audio']
                    if not reason and not path_value:
                        reason = 'Selected audio is not downloaded or exported yet'
                    if reason:
                        report['omitted'].append({**info, 'status': 'omitted', 'detail': reason})
                        continue
                    try:
                        path = Path(path_value).resolve(strict=True)
                        if not path.is_relative_to(library.root) or not path.is_file():
                            raise OSError('Audio is outside the managed library')
                        size = path.stat().st_size
                        if size > maximum - total_bytes:
                            report['omitted'].append({**info, 'status': 'omitted', 'detail': 'Selection exceeds the 10 GB archive limit; export a smaller batch'})
                            continue
                        # Atomic render publication replaces the inode, leaving
                        # this hard link stable even if a new job starts later.
                        snapshot = temporary / f'{index:04d}{path.suffix}'
                        os.link(path, snapshot)
                    except OSError:
                        report['omitted'].append({**info, 'status': 'omitted', 'detail': 'Audio could not be safely snapshotted; check its file and filesystem'})
                        continue
                    total_bytes += size
                    safe_show = re.sub(r'[^\w .-]', '', info['podcast']).strip('. ')[:80] or 'Podcast'
                    safe_title = re.sub(r'[^\w .-]', '', info['title']).strip('. ')[:100] or 'Episode'
                    archive_name = f'{safe_show}/{index + 1:04d}-{safe_title}-{variant}{path.suffix}'
                    info.update(status='included', version=variant, file=archive_name, bytes=size,
                                approved_cut_count=sum(bool(cut.get('approved')) for cut in record['cuts']) if variant == 'cleaned' else 0,
                                pending_review=any(not cut.get('approved') for cut in record['cuts']))
                    snapshots.append((snapshot, archive_name, info, record['transcript'] is not None))
                # Reserve the complete audio selection before deciding which
                # transcripts fit. Each transcript is serialized to its own
                # bounded disk snapshot while the same edit lock is held.
                for index, (snapshot, archive_name, info, has_transcript) in enumerate(snapshots):
                    transcript_path = None
                    if body.include_transcripts and has_transcript:
                        candidate = temporary / f'{index:04d}.transcript.json'
                        try:
                            full_record = library.get(info['episode_id'])
                            source = processing.validate_transcript(full_record['transcript'], full_record['duration'])
                            if info['version'] == 'cleaned':
                                source = processing.cleaned_transcript(source, full_record['cuts'])
                            # Imported metadata is not part of a portable transcript.
                            transcript = {'duration': source['duration'], 'language': source.get('language') if isinstance(source.get('language'), str) else None,
                                          'segments': [{key: segment[key] for key in ('id', 'start', 'end', 'text') if key in segment}
                                                       for segment in source['segments']]}
                            size, omission = 0, None
                            individual_limit = 25 * 1024**2
                            remaining = maximum - total_bytes - transcript_bytes
                            encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False)
                            with candidate.open('wb') as stream:
                                for fragment in encoder.iterencode(transcript):
                                    encoded = fragment.encode('utf-8')
                                    if len(encoded) > min(individual_limit, remaining) - size:
                                        omission = ('Transcript exceeds the 25 MB export limit' if size + len(encoded) > individual_limit else
                                                    'Transcript would exceed the archive payload limit; audio was retained')
                                        break
                                    stream.write(encoded)
                                    size += len(encoded)
                            if omission:
                                info['transcript_omitted'] = omission
                            else:
                                transcript_path = candidate
                                transcript_bytes += size
                                info['transcript_file'] = str(Path(archive_name).with_suffix('')).replace('\\', '/') + '.transcript.json'
                                info['transcript_bytes'] = size
                        except (ValueError, TypeError, KeyError):
                            info['transcript_omitted'] = 'Transcript timestamps are invalid; audio was retained'
                        finally:
                            full_record = source = transcript = None
                            if transcript_path is None:
                                candidate.unlink(missing_ok=True)
                    snapshots[index] = (snapshot, archive_name, info, transcript_path)
            # Transcript snapshots already occupy disk. The ZIP needs another
            # complete payload copy, plus room for the manifest and headers.
            if shutil.disk_usage(export_root).free < total_bytes + transcript_bytes + 16 * 1024**2:
                raise ValueError('Not enough disk space to build this archive. Export a smaller batch or free space.')
            archive_path = temporary / 'castwell-episodes.zip'
            with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for snapshot, archive_name, info, transcript_path in snapshots:
                    archive.write(snapshot, archive_name)
                    if transcript_path is not None:
                        archive.write(transcript_path, info['transcript_file'])
                    report['included'].append(info)
                report['included_count'], report['omitted_count'] = len(report['included']), len(report['omitted'])
                report['audio_bytes'] = total_bytes
                report['transcript_bytes'] = transcript_bytes
                report['payload_bytes'] = total_bytes + transcript_bytes
                archive.writestr('report.json', json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False).encode('utf-8'))
            def release_archive():
                try:
                    workspace.cleanup()
                finally:
                    export_slots.release()
            return ManagedArchiveResponse(archive_path, media_type='application/zip', filename='castwell-episodes.zip',
                                headers={'X-Castwell-Included': str(len(report['included'])), 'X-Castwell-Omitted': str(len(report['omitted']))},
                                cleanup=release_archive)
        except Exception as exc:
            try:
                workspace.cleanup()
            finally:
                export_slots.release()
            if isinstance(exc, OSError):
                raise ValueError('Archive creation failed. Check disk space and filesystem permissions.') from None
            raise

    @app.post('/api/export/archive')
    def export_archive(body: ArchiveRequest):
        return build_archive(body)

    @app.post('/api/export/archive/form')
    def export_archive_form(payload: str = Form(..., max_length=128 * 1024)):
        try:
            body = ArchiveRequest.model_validate_json(payload)
        except ValidationError:
            raise ValueError('Choose up to 500 episodes and a supported audio version for export') from None
        return build_archive(body)

    @app.get('/api/export/feed')
    def export_library_feed(request: Request):
        root = ET.Element('rss', version='2.0')
        channel = ET.SubElement(root, 'channel')
        ET.SubElement(channel, 'title').text = 'Castwell · Prepared episodes'
        ET.SubElement(channel, 'link').text = str(request.base_url)
        ET.SubElement(channel, 'description').text = 'Your prepared podcast library. Edited copies include approved cuts; originals are used for reviewed episodes with no cuts.'
        for record in library.all(include_large=False):
            if record['archived']:
                continue
            public = library.public(record)
            if record.get('ad_mode') == 'keep' and public['has_audio']:
                variant, path = 'original', Path(record['audio'])
            elif public['preferred_version'] == 'cleaned':
                variant, path = 'cleaned', Path(record['cleaned'])
            elif public['has_audio'] and record['analysis_done'] and not record['cuts'] and record['status'] == 'ready':
                variant, path = 'original', Path(record['audio'])
            else:
                continue
            item = ET.SubElement(channel, 'item')
            ET.SubElement(item, 'title').text = record['title']
            ET.SubElement(item, 'guid', isPermaLink='false').text = record['id']
            ET.SubElement(item, 'description').text = f"{record['podcast']} - {variant} audio. {record.get('description', '')}"
            ET.SubElement(item, 'enclosure', url=str(request.base_url) + f"media/{record['id']}/{variant}", length=str(path.stat().st_size), type=mimetypes.guess_type(path)[0] or 'audio/mpeg')
        return Response(ET.tostring(root, encoding='utf-8', xml_declaration=True), media_type='application/rss+xml', headers={'Content-Disposition': 'attachment; filename="castwell-prepared.xml"'})

    static = Path(__file__).parent / 'static'
    app.mount('/static', StaticFiles(directory=static, check_dir=False), name='assets')
    app.mount('/', StaticFiles(directory=static, html=True, check_dir=False), name='gallery')
    return app
