"""One durable, resumable audio pipeline with cooperative cancellation.

Each expensive stage commits its completed artifact before the next begins.
Cancelling a job therefore never throws away its original audio or transcript.
"""

from __future__ import annotations

from concurrent.futures import Future
import json
import os
from pathlib import Path
import queue
import threading
import uuid
from urllib.parse import urlsplit

from . import processing
from .feeds import download_media


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
            self.library.get(episode_id)
            if self._closed or episode_id in self.pending:
                return False
            self.pending.add(episode_id)
            self._cancellations[episode_id] = threading.Event()
            self.library.update(episode_id, status='queued', error=None, progress='Waiting to process')
            try:
                # The worker takes this same lock before starting. Its future is
                # registered before cancel() or the worker can observe the job.
                self._futures[episode_id] = self.pool.submit(self.run, episode_id, operation, options)
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
                episode = self.library.get(episode_id)
                records.append({'episode_id': episode_id, 'title': episode.get('title', 'Untitled episode'),
                                'status': episode['status'], 'progress': episode['progress'], 'position': position})
            return records

    @staticmethod
    def _option(options, name, default):
        value = options.get(name, default) if isinstance(options, dict) else getattr(options, name, default)
        return default if value is None else value

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

    def run(self, episode_id, operation, options=None):
        with self.lock:
            cancel = self._cancellations.get(episode_id, threading.Event())
        try:
            self._check_cancel(cancel)
            settings = dict(self.settings_getter() if self.settings_getter else {})
            if operation in ('process', 'download'):
                self._process(episode_id, options, settings, cancel, download_only=operation == 'download')
            else:
                self.render(episode_id, cancel)
        except processing.ProcessingCancelled:
            with self.lock:
                self._cancelled(episode_id)
        except Exception as exc:
            # Media/provider failures must not disclose subscription URLs or keys.
            message = str(exc)
            if '://' in message or len(message) > 800:
                message = f'{type(exc).__name__}: processing failed. Check model configuration and media availability.'
            with self.lock:
                episode = self.library.get(episode_id)
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
        duration = processing.probe_duration(audio)
        self.library.update(episode_id, duration=duration)
        self._check_cancel(cancel)
        if not episode.get('waveform'):
            self.progress(episode_id, 'downloading', 'Preparing audio waveform')
            waveform = processing.waveform(audio, bins=700, should_cancel=cancel.is_set)
            self.library.update(episode_id, waveform=waveform)
        self._check_cancel(cancel)
        if download_only or self._option(options, 'download_only', False):
            self.library.update(episode_id, status='downloaded', progress='Original audio ready to play')
            return

        episode = self.library.get(episode_id)
        if episode['transcript'] is None:
            self.progress(episode_id, 'transcribing', 'Generating timestamped transcript')
            transcript = processing.transcribe(
                audio, model=settings.get('transcription_model') or self.model,
                language=settings.get('language') or None,
                model_cache=settings.get('model_cache') or None,
                progress=lambda message: self.progress(episode_id, 'transcribing', message),
                should_cancel=cancel.is_set,
            )
            transcript = processing.validate_transcript(transcript, duration)
            self.library.update(episode_id, transcript=transcript)
        else:
            # Imported transcripts may carry the feed's estimated duration.
            transcript = processing.validate_transcript(episode['transcript'], duration)
            self.library.update(episode_id, transcript=transcript)
        self._check_cancel(cancel)

        if not self._option(options, 'remove_ads', True):
            has_speech = bool(transcript['segments'])
            self.library.update(episode_id, status='ready' if has_speech else 'review',
                                progress='Transcript ready' if has_speech else 'No speech detected; review original audio')
            return

        episode = self.library.get(episode_id)
        # Empty, reviewed decisions are still decisions. Only an explicit
        # redetection may replace them or an existing completed no-ad analysis.
        needs_detection = self._option(options, 'redetect', False) or (
            not episode.get('analysis_done') and not episode['cuts'])
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
            self.library.update(episode_id, cuts=cuts, analysis_done=True, cleaned=None,
                                cleaned_duration=0, removed_seconds=0)
        self._check_cancel(cancel)
        self.render(episode_id, cancel)

    def render(self, episode_id, cancel=None):
        cancel = cancel or threading.Event()
        self._check_cancel(cancel)
        episode = self.library.get(episode_id)
        if not episode.get('audio') or not Path(episode['audio']).is_file():
            raise ValueError('Download the original audio before exporting cuts.')
        cuts = episode['cuts']
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
        no_speech = episode['transcript'] is not None and not episode['transcript']['segments']
        review = no_speech or any(not cut.get('approved') for cut in cuts)
        message = ('No speech detected; review original audio' if no_speech else
                   'Review suggested cuts' if review else
                   'Approved cuts exported' if has_approved else
                   'No cuts selected; original audio retained')
        self.library.update(episode_id, status='review' if review else 'ready', progress=message)
