"""Local gallery and a serialized, resumable audio processing queue."""
import json
import hashlib
import re
import tempfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
import mimetypes
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import processing
from .feeds import read_feed
from .library import ACTIVE_STATUSES, Library
from .jobs import Jobs, SerialWorker
from .config import Settings, warm_transcription_model
from .middleware import AllowedHosts, RequestSizeLimit


class FeedRequest(BaseModel):
    url: str = Field(min_length=1, max_length=8192)


class ProcessRequest(BaseModel):
    remove_ads: bool = True
    detector: str | None = None
    redetect: bool = False
    download_only: bool = False


class BatchRequest(ProcessRequest):
    ids: list[str] = Field(min_length=1, max_length=500)


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

    @asynccontextmanager
    async def lifespan(app):
        library.recover()
        yield
        jobs.shutdown()
        model_work.shutdown()

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
        response.headers['Referrer-Policy'] = 'no-referrer'
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

    @app.get('/api/episodes')
    def episodes():
        records = library.summaries()
        return {'episodes': records, 'settings': settings.get(), 'stats': {
            'episodes': len(records), 'ready': sum(item['status'] == 'ready' for item in records),
            'processing': sum(item['status'] in ACTIVE_STATUSES for item in records),
            'removed_seconds': sum(item['removed_seconds'] for item in records),
        }}

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
            values = {'last_checked': _timestamp_now(), 'error': None}
            if entries:
                values.update(title=entries[0]['podcast'], image=entries[0].get('image', ''))
            library.mark_feed(feed_id, **values)
            return {'added': library.import_entries(entries), 'total': len(entries)}
        except ValueError as exc:
            library.mark_feed(feed_id, last_checked=_timestamp_now(), error=str(exc))
            raise

    @app.delete('/api/feeds/{feed_id}')
    def unsubscribe(feed_id: str):
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
            library.update(episode_id, cuts=cuts, cleaned=None, cleaned_duration=0, removed_seconds=0, analysis_done=True, status='review', error=None, progress='Earlier cuts restored. Export to apply them.')
            return library.public(library.get(episode_id), detail=True)

    @app.get('/api/episodes/{episode_id}/decisions')
    def export_decisions(episode_id: str):
        record = library.get(episode_id)
        body = {'format': 'castwell-decisions', 'version': 1, 'episode': {'id': episode_id, 'title': record['title'], 'podcast': record['podcast'], 'duration': record['duration']}, 'timeline': 'original', 'cuts': record['cuts'], 'transcript': record['transcript']}
        return Response(json.dumps(body, indent=2, ensure_ascii=False), media_type='application/json', headers={'Content-Disposition': f'attachment; filename="{episode_id}-decisions.json"'})

    @app.get('/api/jobs')
    def queue():
        return {'jobs': jobs.snapshot()}

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

    @app.post('/api/episodes/{episode_id}/process', status_code=202)
    def process(episode_id: str, body: ProcessRequest):
        check_detector(body.detector)
        return {'queued': jobs.submit(episode_id, 'process', body)}

    @app.post('/api/process', status_code=202)
    def batch(body: BatchRequest):
        check_detector(body.detector)
        ids = list(dict.fromkeys(body.ids))
        for episode_id in ids:
            library.get(episode_id)
        return {'queued': sum(jobs.submit(episode_id, 'process', body) for episode_id in ids)}

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
            library.update(episode_id, cuts=cuts, cleaned=None, cleaned_duration=0, removed_seconds=0, analysis_done=True, status='review', error=None, progress='Cut changes saved. Export to apply them.')
            return library.public(library.get(episode_id), detail=True)

    @app.post('/api/episodes/{episode_id}/render', status_code=202)
    def render(episode_id: str):
        record = library.get(episode_id)
        if not record['audio']:
            raise ValueError('Process the episode to download audio first')
        return {'queued': jobs.submit(episode_id, 'render')}

    @app.post('/api/episodes/{episode_id}/transcript')
    def import_transcript(episode_id: str, body: dict):
        with jobs.lock:
            ensure_idle(episode_id)
            record = library.get(episode_id)
            transcript = processing.validate_transcript(body, record['duration'] or None)
            if record['cuts']:
                library.save_revision(episode_id, record['cuts'], 'Before replacing the transcript')
            library.update(episode_id, transcript=transcript, cuts=[], cleaned=None, cleaned_duration=0, removed_seconds=0, analysis_done=False, error=None, status='available', progress='Transcript imported. Process to detect ads.')
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

    @app.get('/api/export/feed')
    def export_library_feed(request: Request):
        root = ET.Element('rss', version='2.0')
        channel = ET.SubElement(root, 'channel')
        ET.SubElement(channel, 'title').text = 'Castwell · Prepared episodes'
        ET.SubElement(channel, 'link').text = str(request.base_url)
        ET.SubElement(channel, 'description').text = 'Your prepared podcast library. Edited copies include approved cuts; originals are used for reviewed episodes with no cuts.'
        for record in library.all():
            if record['archived']:
                continue
            public = library.public(record)
            if public['has_cleaned']:
                variant, path = 'cleaned', Path(record['cleaned'])
            elif public['has_audio'] and record['analysis_done'] and not record['cuts'] and record['status'] == 'ready':
                variant, path = 'original', Path(record['audio'])
            else:
                continue
            item = ET.SubElement(channel, 'item')
            ET.SubElement(item, 'title').text = record['title']
            ET.SubElement(item, 'guid', isPermaLink='false').text = record['id']
            ET.SubElement(item, 'description').text = f"{record['podcast']} — {variant} audio. {record.get('description', '')}"
            ET.SubElement(item, 'enclosure', url=str(request.base_url) + f"media/{record['id']}/{variant}", length=str(path.stat().st_size), type=mimetypes.guess_type(path)[0] or 'audio/mpeg')
        return Response(ET.tostring(root, encoding='utf-8', xml_declaration=True), media_type='application/rss+xml', headers={'Content-Disposition': 'attachment; filename="castwell-prepared.xml"'})

    static = Path(__file__).parent / 'static'
    app.mount('/static', StaticFiles(directory=static, check_dir=False), name='assets')
    app.mount('/', StaticFiles(directory=static, html=True, check_dir=False), name='gallery')
    return app
