"""One durable, resumable audio pipeline with cooperative cancellation.

Each expensive stage commits its completed artifact before the next begins.
Cancelling a job therefore never throws away its original audio or transcript.
"""

from __future__ import annotations

from concurrent.futures import Future
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import queue
import threading
import uuid
from urllib.parse import urlsplit

from . import processing
from .feeds import download_media


PROCESS_DEFAULTS = {'remove_ads': None, 'detector': None, 'redetect': False,
                    'retranscribe': False, 'download_only': False}


class SerialWorker:
    """A single daemon worker; uninterruptible model loads cannot block exit.

    Future objects retain the usual queued cancellation contract. During normal
    shutdown the job's cancellation event stops work at its next safe boundary;
    atomic file writes and SQLite transactions also tolerate process termination.
    """

    def __init__(self, name='castwell-worker'):
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(self, function, *args):
        with self._lock:
            if self._closed:
                raise RuntimeError('The processing worker has stopped')
            future = Future()
            self._queue.put((future, function, args))
            return future

    def _run(self):
        while True:
            work = self._queue.get()
            if work is None:
                return
            future, function, args = work
            if not future.set_running_or_notify_cancel():
                continue
            try:
                result = function(*args)
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)

    def shutdown(self, wait=True, cancel_futures=False):
        with self._lock:
            if not self._closed:
                self._closed = True
                if cancel_futures:
                    while True:
                        try:
                            future, _, _ = self._queue.get_nowait()
                        except queue.Empty:
                            break
                        future.cancel()
                self._queue.put(None)
        if wait and threading.current_thread() is not self._thread:
            self._thread.join()


class Jobs:
    def __init__(self, library, model, settings_getter=None):
        self.library = library
        self.model = model or os.getenv('CASTWELL_TRANSCRIPTION_MODEL', 'base')
        self.settings_getter = settings_getter
        self.pool = SerialWorker()
        self.lock = threading.RLock()
        self.pending = set()
        self._futures = {}
        self._cancellations = {}
        self._closed = False

    def submit(self, episode_id, operation, options=None):
        if operation not in ('process', 'render', 'download'):
            raise ValueError('Unknown processing operation')
        with self.lock:
            episode = self.library.get(episode_id, include_large=False)
            if self._closed or episode_id in self.pending:
                return False
            supplied = {key: self._option(options, key, default) for key, default in PROCESS_DEFAULTS.items()}
            if supplied['remove_ads'] is not None and not isinstance(supplied['remove_ads'], bool):
                raise ValueError('Ad removal must be true, false, or omitted to use the podcast preference')
            if supplied['download_only'] and (supplied['redetect'] or supplied['retranscribe']):
                raise ValueError('Audio-only downloads cannot also request transcription or ad detection')
            previous = episode.get('pending_job')
            retry_defaults = dict(supplied, remove_ads=None)
            same_ad_choice = previous and (supplied['remove_ads'] is None or
                                           supplied['remove_ads'] == previous.get('options', {}).get('remove_ads'))
            if previous and operation == 'process' and retry_defaults == PROCESS_DEFAULTS and same_ad_choice:
                # Retry resumes the unfinished operation, including after a
                # restart. Successful stages clear their own redo flags.
                request = previous
                operation, supplied = request['operation'], request['options']
            else:
                # Resolve the show preference before queueing. Later preference
                # changes cannot alter an in-flight job or its durable retry.
                if supplied['remove_ads'] is None:
                    supplied['remove_ads'] = episode.get('ad_mode', 'remove') == 'remove'
                request = {'operation': operation, 'options': supplied,
                           'prior_status': episode['status'], 'prior_progress': episode['progress']}
                selected_settings = self.settings_getter() if self.settings_getter else {}
                request['approval_policy'] = self._approval_policy(selected_settings)
                if operation == 'process' and supplied['remove_ads'] and not supplied['download_only']:
                    request['detection_policy'] = self._detection_policy(selected_settings, supplied.get('detector'))
            self.library.update(episode_id, status='queued', error=None, progress='Waiting to process', pending_job=request)
            self.pending.add(episode_id)
            self._cancellations[episode_id] = threading.Event()
            try:
                # The worker takes this same lock before starting. Its future is
                # registered before cancel() or the worker can observe the job.
                self._futures[episode_id] = self.pool.submit(self.run, episode_id, operation, supplied)
            except Exception:
                self._forget(episode_id)
                self.library.update(episode_id, status='error', error='The processing queue is unavailable.',
                                    progress='Could not queue this episode. Retry to resume.')
                raise
            return True

    def _forget(self, episode_id):
        self.pending.discard(episode_id)
        self._futures.pop(episode_id, None)
        self._cancellations.pop(episode_id, None)

    def _cancelled(self, episode_id):
        self.library.update(episode_id, status='cancelled', error=None,
                            progress='Cancelled. Completed audio and transcript are saved; retry to resume.')

    def cancel(self, episode_id):
        with self.lock:
            self.library.get(episode_id)
            if episode_id not in self.pending:
                return False
            self._cancellations[episode_id].set()
            future = self._futures[episode_id]
            if future.cancel():
                # A cancelled future never enters run(), so cleanup belongs here.
                self._cancelled(episode_id)
                self._forget(episode_id)
            else:
                self.library.update(episode_id, progress='Cancelling; waiting for the current operation to stop.')
            return True

    def shutdown(self):
        """Request cancellation without blocking server shutdown on model work."""
        with self.lock:
            if self._closed:
                return
            self._closed = True
            for episode_id in list(self.pending):
                self.cancel(episode_id)
        self.pool.shutdown(wait=False, cancel_futures=True)

    def snapshot(self):
        with self.lock:
            records = []
            for position, episode_id in enumerate(self._futures, start=1):
                episode = self.library.get(episode_id, include_large=False)
                records.append({'episode_id': episode_id, 'title': episode.get('title', 'Untitled episode'),
                                'status': episode['status'], 'progress': episode['progress'], 'position': position,
                                'operation': (episode.get('pending_job') or {}).get('operation', 'process'),
                                'remove_ads': (episode.get('pending_job') or {}).get('options', {}).get('remove_ads'),
                                'ad_mode': 'remove' if (episode.get('pending_job') or {}).get('options', {}).get('remove_ads') else 'keep'})
            return records

    @staticmethod
    def _option(options, name, default):
        value = options.get(name, default) if isinstance(options, dict) else getattr(options, name, default)
        return default if value is None else value

    @staticmethod
    def _approval_policy(settings):
        # Persist only the two approval controls, never connection credentials.
        review = settings.get('review_only', False)
        threshold = settings.get('auto_approve_threshold', .90)
        if (not isinstance(review, bool) or isinstance(threshold, bool)
                or not isinstance(threshold, (int, float)) or not math.isfinite(threshold)
                or not 0 <= threshold <= 1):
            raise ValueError('Invalid approval policy')
        return {'review_only': review, 'auto_approve_threshold': float(threshold)}

    @classmethod
    def _detection_policy(cls, settings, detector=None):
        """Snapshot only validated, nonsecret choices and request-profile identity."""
        from .ad_scope import REMOVAL_SCOPE
        policy = settings.get('ai_policy', 'legacy')
        selected_detector = detector or settings.get('detector') or 'auto'
        reasoning = settings.get('ai_reasoning', False)
        address = settings.get('ai_base_url', '')
        model = settings.get('ai_model', '')
        if (policy not in {'legacy', 'verified', 'scene'} or selected_detector not in {'auto', 'ai', 'heuristic'}
                or not isinstance(reasoning, bool) or not isinstance(address, str) or not isinstance(model, str)):
            raise ValueError('Invalid detector policy')
        address, model = address.strip().rstrip('/'), model.strip()
        if address:
            try:
                parsed = urlsplit(address)
                parsed.port
                if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username is not None
                        or parsed.password is not None or parsed.query or parsed.fragment
                        or any(char.isspace() for char in address) or '\\' in address):
                    raise ValueError
            except ValueError:
                raise ValueError('Classifier address cannot contain credentials or query parameters') from None
        if len(model) > 256 or any(ord(char) < 32 for char in model):
            raise ValueError('Invalid classifier model')
        version, profile = processing.LEGACY_AI_POLICY_VERSION, 'legacy-per-unit'
        if selected_detector == 'heuristic':
            version, profile = 'commercial-cues-unversioned', 'commercial-cues'
        elif policy == 'verified':
            from .ad_review import POLICY_VERSION, inference_settings
            version = POLICY_VERSION
            encoded = json.dumps(inference_settings(reasoning), sort_keys=True, separators=(',', ':'))
            profile = 'verified-' + hashlib.sha256(encoded.encode()).hexdigest()
        elif policy == 'scene':
            from .scene_review import POLICY_VERSION, INFERENCE_PROFILE
            version, profile = POLICY_VERSION, INFERENCE_PROFILE
            from .scene_pipeline import POLICY_VERSION as PIPELINE_VERSION
            version = PIPELINE_VERSION + '+' + version
            # The optional endpoint audit is a separate policy component. Its
            # arrival or revision invalidates an older queued profile explicitly.
            try:
                edges = importlib.import_module('.scene_edges', package='castwell')
            except ModuleNotFoundError as exc:
                if exc.name != 'castwell.scene_edges':
                    raise
            else:
                version += '+' + edges.POLICY_VERSION
                profile += '+' + edges.INFERENCE_PROFILE
        return {**cls._approval_policy(settings), 'detector': selected_detector, 'ai_policy': policy,
                'ai_reasoning': reasoning, 'ai_base_url': address, 'ai_model': model,
                'policy_version': version, 'inference_profile': profile, 'removal_scope': REMOVAL_SCOPE}

    @classmethod
    def _restore_detection_policy(cls, snapshot):
        if not isinstance(snapshot, dict):
            raise ValueError('Invalid saved detector policy')
        current = cls._detection_policy(snapshot)
        if current != snapshot:
            raise processing.ProcessingError('The detector profile changed since this job was queued. Choose Find ads again to use the current detector; existing edits are retained.')
        return current

    @staticmethod
    def _check_cancel(cancel):
        if cancel.is_set():
            raise processing.ProcessingCancelled('Processing cancelled')

    def progress(self, episode_id, status, message):
        with self.lock:
            cancel = self._cancellations.get(episode_id)
            if cancel:
                self._check_cancel(cancel)
            self.library.update(episode_id, status=status, progress=str(message))

    def _complete_stage(self, episode_id, option, **values):
        request = self.library.get(episode_id, include_large=False).get('pending_job')
        if request:
            request['options'][option] = False
            values['pending_job'] = request
        self.library.update(episode_id, **values)

    def run(self, episode_id, operation, options=None):
        with self.lock:
            cancel = self._cancellations.get(episode_id, threading.Event())
        try:
            self._check_cancel(cancel)
            settings = dict(self.settings_getter() if self.settings_getter else {})
            request = self.library.get(episode_id, include_large=False).get('pending_job') or {}
            if 'approval_policy' in request:
                # Queued work and retries keep the listener's original policy.
                settings.update(self._approval_policy(request['approval_policy']))
            if ('detection_policy' in request and operation == 'process'
                    and self._option(options, 'remove_ads', True) and not self._option(options, 'download_only', False)):
                settings.update(self._restore_detection_policy(request['detection_policy']))
            # Legacy pending jobs intentionally retain their original behavior:
            # approval controls stay frozen, other choices use current settings.
            if operation in ('process', 'download'):
                self._process(episode_id, options, settings, cancel, download_only=operation == 'download')
            else:
                self.render(episode_id, cancel)
            with self.lock:
                self._check_cancel(cancel)
                self.library.update(episode_id, pending_job=None)
        except processing.ProcessingCancelled:
            with self.lock:
                self._cancelled(episode_id)
        except Exception as exc:
            # Media/provider failures must not disclose subscription URLs or keys.
            message = str(exc)
            if '://' in message or len(message) > 800:
                message = f'{type(exc).__name__}: processing failed. Check model configuration and media availability.'
            with self.lock:
                episode = self.library.get(episode_id, include_large=False)
                phase = episode['status']
                self.library.update(episode_id, status='error', error=message,
                                    progress=f'Failed during {phase}. Completed steps are saved; retry to resume.')
        finally:
            with self.lock:
                # A cancellation racing the final status update still wins, and
                # a subsequent submit cannot overlap the worker's final cleanup.
                if cancel.is_set():
                    self._cancelled(episode_id)
                self._forget(episode_id)

    def _process(self, episode_id, options, settings, cancel, download_only=False):
        episode = self.library.get(episode_id)
        folder = self.library.directory(episode_id)
        audio = Path(episode['audio']) if episode.get('audio') else None
        same_audio = audio is not None and audio.is_file()
        if audio is None or not audio.is_file():
            self.progress(episode_id, 'downloading', 'Downloading original audio')
            if audio is not None:
                # A podcast host may insert different ads into a fresh download.
                # Old timestamps cannot safely follow the replacement recording.
                # Invalidate before downloading, including cancellation just after
                # publication; retain the old transcript and edit history locally.
                if episode['transcript'] is not None:
                    backup = folder / ('previous-transcript-' + uuid.uuid4().hex + '.json')
                    backup.write_text(json.dumps(episode['transcript'], ensure_ascii=False, indent=2), encoding='utf-8')
                if episode['cuts']:
                    self.library.save_revision(episode_id, episode['cuts'], 'Before replacing missing original audio; timestamps belong to the previous recording')
                self.library.update(episode_id, audio=None, transcript=None, cuts=[], waveform=None,
                                    cleaned=None, cleaned_duration=0, removed_seconds=0, duration=0,
                                    analysis_done=False, position=0, played=False)
                episode = self.library.get(episode_id)
            extension = Path(urlsplit(episode.get('media_url') or '').path).suffix.lower()
            if extension not in ('.mp3', '.m4a', '.mp4', '.ogg', '.opus', '.wav', '.flac', '.aac'):
                extension = '.audio'
            audio = download_media(episode.get('media_url'), folder / ('original' + extension),
                                   progress=lambda message: self.progress(episode_id, 'downloading', message),
                                   should_cancel=cancel.is_set)
            # Even a cancellation during the following probe retains this file.
            self.library.update(episode_id, audio=str(audio))
        self._check_cancel(cancel)
        self.progress(episode_id, 'downloading', 'Reading audio duration')
        try:
            duration = processing.probe_duration(audio)
        except processing.InvalidAudioError:
            # A host can return an HTTP 200 error page as media. Remove only
            # our cached download, so Retry fetches fresh bytes. Retain the DB
            # path so replacement invalidates old timestamps on the next run.
            if (episode.get('media_url') and audio.parent.resolve() == folder.resolve()
                    and audio.name.startswith('original.')):
                audio.unlink(missing_ok=True)
            raise
        self.library.update(episode_id, duration=duration)
        self._check_cancel(cancel)
        if not episode.get('waveform'):
            self.progress(episode_id, 'downloading', 'Preparing audio waveform')
            waveform = processing.waveform(audio, bins=700, should_cancel=cancel.is_set)
            self.library.update(episode_id, waveform=waveform)
        self._check_cancel(cancel)
        if download_only or self._option(options, 'download_only', False):
            request = episode.get('pending_job') or {}
            if same_audio and request.get('prior_status') in ('ready', 'review'):
                self.library.update(episode_id, status=request['prior_status'], progress=request['prior_progress'])
            elif same_audio and episode.get('analysis_done') and not episode['cuts']:
                self.library.update(episode_id, status='ready', progress='No cuts selected; original audio retained')
            else:
                self.library.update(episode_id, status='downloaded', progress='Original audio ready to play')
            return

        episode = self.library.get(episode_id)
        if episode['transcript'] is None or self._option(options, 'retranscribe', False):
            self.progress(episode_id, 'transcribing', 'Generating timestamped transcript')
            transcript = processing.transcribe(
                audio, model=settings.get('transcription_model') or self.model,
                language=settings.get('language') or None,
                model_cache=settings.get('model_cache') or None,
                progress=lambda message: self.progress(episode_id, 'transcribing', message),
                should_cancel=cancel.is_set,
            )
            transcript = processing.validate_transcript(transcript, duration)
            if episode['transcript'] is not None:
                backup = folder / ('previous-transcript-' + uuid.uuid4().hex + '.json')
                backup.write_text(json.dumps(episode['transcript'], ensure_ascii=False, indent=2), encoding='utf-8')
            # The original timeline has not changed, so reviewed cuts remain
            # valid. Failed or cancelled inference never clears the old text.
            self._complete_stage(episode_id, 'retranscribe', transcript=transcript)
        else:
            # Imported transcripts may carry the feed's estimated duration.
            transcript = processing.validate_transcript(episode['transcript'], duration)
            self.library.update(episode_id, transcript=transcript)
        self._check_cancel(cancel)

        if not self._option(options, 'remove_ads', True):
            has_speech = bool(transcript['segments'])
            self.library.update(episode_id, status='ready',
                                progress='Original audio and transcript ready; ads retained' if has_speech else 'Original audio ready; no speech detected; ads retained')
            return

        episode = self.library.get(episode_id)
        # Empty, reviewed decisions are still decisions. Only an explicit
        # redetection may replace them or an existing completed no-ad analysis.
        needs_detection = self._option(options, 'redetect', False) or (
            not episode.get('analysis_done') and not episode['cuts'])
        profile = self._detection_policy(settings, self._option(options, 'detector', None))
        if (not needs_detection and episode.get('analysis_profile') is not None
                and episode['analysis_profile'] != profile):
            raise processing.ProcessingError('This episode was analyzed with a different detector profile. Choose Find ads again to replace that analysis; existing edits are retained.')
        if needs_detection and transcript['segments']:
            self.progress(episode_id, 'detecting', 'Looking for advertisements in context')
            detector = self._option(options, 'detector', settings.get('detector') or 'auto')
            cuts = processing.detect_ads(
                transcript, detector=detector, config=settings,
                progress=lambda message: self.progress(episode_id, 'detecting', message),
                should_cancel=cancel.is_set,
            )
            cuts = processing.validate_cuts(cuts, duration)
            if cuts != episode['cuts']:
                self.library.save_revision(episode_id, episode['cuts'], 'Before advertisement detection')
            self._complete_stage(episode_id, 'redetect', cuts=cuts, analysis_done=True, analysis_profile=profile, cleaned=None,
                                 cleaned_duration=0, removed_seconds=0)
        self._check_cancel(cancel)
        self.render(episode_id, cancel, automatic=True)

    def render(self, episode_id, cancel=None, *, automatic=False):
        cancel = cancel or threading.Event()
        self._check_cancel(cancel)
        episode = self.library.get(episode_id)
        if not episode.get('audio') or not Path(episode['audio']).is_file():
            raise ValueError('Download the original audio before exporting cuts.')
        cuts = episode['cuts']
        no_speech = (episode['transcript'] is not None and not episode['transcript']['segments']
                     and not self.library.coverage_review_current(episode))
        coverage_review = bool(episode.get('transcript_review'))
        review = no_speech or coverage_review or any(not cut.get('approved') for cut in cuts)
        if automatic and review:
            message = ('No speech detected; review original audio' if no_speech else
                       'Transcript coverage needs review; original audio retained' if coverage_review else
                       'Review suggested cuts; original audio retained')
            self.library.update(episode_id, status='review', progress=message)
            return
        has_approved = any(cut.get('approved') for cut in cuts)
        if has_approved:
            self.progress(episode_id, 'rendering', 'Exporting audio with approved cuts')
            output = self.library.directory(episode_id) / 'cleaned.mp3'
            result = processing.render_audio(Path(episode['audio']), output, cuts, should_cancel=cancel.is_set)
            self.library.update(episode_id, cleaned=str(output), cleaned_duration=result['duration'],
                                removed_seconds=result['removed_seconds'])
        else:
            self.library.update(episode_id, cleaned=None, cleaned_duration=0, removed_seconds=0)
        self._check_cancel(cancel)
        message = ('No speech detected; review original audio' if no_speech else
                   'Transcript coverage needs review; edited audio is a manual preview' if coverage_review else
                   'Review suggested cuts' if review else
                   'Approved cuts exported' if has_approved else
                   'No cuts selected; original audio retained')
        self.library.update(episode_id, status='review' if review else 'ready', progress=message)
