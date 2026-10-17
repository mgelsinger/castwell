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


def _fast_audit_document(audits, transcript, cuts, profile):
    """Keep complete typed evidence tied to this exact analysis, without keys."""
    from .fast_review import INFERENCE_PROFILE
    from .episode_boundaries import (POLICY_VERSION as BOUNDARY_VERSION, GLOBAL_LABELS, WORD_LABELS,
                                     build_boundary_plan, _render_request)
    from .episode_omissions import omission_guard
    fields = {'policy_version', 'removal_scope', 'inference_profile', 'global_decisions',
              'boundary_audit', 'word_decisions', 'unresolved_word_ids', 'source_word_count',
              'requires_review', 'automatic_acceptance_validated', 'sweep_requests', 'sweep_votes',
              'omission_guard'}
    if len(audits) != 1 or not isinstance(audits[0], dict) or set(audits[0]) != fields:
        raise processing.ProcessingError('The fast detector did not return one complete review audit; existing decisions are retained.')
    audit = audits[0]
    if (audit['policy_version'] != profile['policy_version'] or audit['removal_scope'] != profile['removal_scope']
            or audit['inference_profile'] != INFERENCE_PROFILE or audit['requires_review'] is not True
            or audit['automatic_acceptance_validated'] is not False):
        raise processing.ProcessingError('The fast detector review audit does not match the queued policy.')
    units = processing._classification_transcript(transcript)['segments']
    original_words = [word for segment in transcript['segments'] for word in segment.get('words', [])]
    source_words = [word for unit in units for word in unit.get('words', [])]
    count = len(original_words)
    if source_words != original_words:
        raise processing.ProcessingError('The fast detector review audit cannot be mapped to every original source word.')
    if type(audit['source_word_count']) is not int or audit['source_word_count'] != count:
        raise processing.ProcessingError('The fast detector review audit changed source word coverage.')
    plan = build_boundary_plan(units, audit['global_decisions'])
    guard = audit['omission_guard']
    if (not isinstance(guard, dict) or guard != omission_guard(units, audit['global_decisions'])
            or guard.get('guard_requires_review') is not False):
        raise processing.ProcessingError('The fast detector omission guard requires review; existing decisions are retained.')
    requests, sweep_votes = audit['sweep_requests'], audit['sweep_votes']
    core_ownership, source_counts = [], {i: 0 for i in range(len(units))}
    if not isinstance(requests, list) or not isinstance(sweep_votes, dict) or set(sweep_votes) != set(source_counts):
        raise processing.ProcessingError('The fast detector sweep audit has incomplete source ownership.')
    for request in requests:
        if not isinstance(request, dict) or set(request) != {'core_ids', 'source_ids'}:
            raise processing.ProcessingError('The fast detector sweep audit has invalid request fields.')
        for name in ('core_ids', 'source_ids'):
            identifiers = request[name]
            if (not isinstance(identifiers, list) or not identifiers
                    or any(type(i) is not int or i not in source_counts for i in identifiers)
                    or identifiers != list(range(identifiers[0], identifiers[-1] + 1))):
                raise processing.ProcessingError('The fast detector sweep audit has invalid source IDs.')
        if not set(request['core_ids']) <= set(request['source_ids']):
            raise processing.ProcessingError('The fast detector sweep audit omitted its own target core.')
        core_ownership.extend(request['core_ids'])
        for identifier in request['source_ids']:
            source_counts[identifier] += 1
    if core_ownership != list(range(len(units))):
        raise processing.ProcessingError('The fast detector sweep audit omitted or duplicated a source core.')
    for identifier, labels in sweep_votes.items():
        if (type(identifier) is not int or not isinstance(labels, list) or not labels
                or len(labels) != source_counts[identifier]
                or any(not isinstance(label, str) or label not in GLOBAL_LABELS for label in labels)
                or audit['global_decisions'][identifier] != (labels[0] if len(set(labels)) == 1 else 'uncertain')):
            raise processing.ProcessingError('The fast detector global decisions disagree with its sweep votes.')
    boundary = audit['boundary_audit']
    boundary_fields = {'policy_version', 'source_word_count', 'target_word_ids', 'scene_blocks',
                       'requests', 'word_votes', 'unresolved_word_ids'}
    expected_scenes = [{key: value for key, value in block.items() if key != 'context_units'}
                       for block in plan['scene_blocks']]
    if (not isinstance(boundary, dict) or set(boundary) != boundary_fields
            or boundary['policy_version'] != BOUNDARY_VERSION
            or type(boundary['source_word_count']) is not int or boundary['source_word_count'] != count
            or boundary['target_word_ids'] != plan['target_word_ids']
            or boundary['scene_blocks'] != expected_scenes or not isinstance(boundary['requests'], list)):
        raise processing.ProcessingError('The fast detector boundary audit changed required scene ownership.')
    owned_scenes = []
    for request in boundary['requests']:
        if (not isinstance(request, dict) or set(request) != {'scene_ids', 'source_word_ids', 'request_sha256'}
                or not isinstance(request['scene_ids'], list) or not request['scene_ids']
                or any(type(i) is not int or not 0 <= i < len(plan['scene_blocks']) for i in request['scene_ids'])):
            raise processing.ProcessingError('The fast detector boundary audit has invalid request ownership.')
        expected_request = _render_request([plan['scene_blocks'][i] for i in request['scene_ids']], plan['words'])
        expected_hash = hashlib.sha256(json.dumps(expected_request, sort_keys=True, separators=(',', ':'),
                                                  allow_nan=False).encode()).hexdigest()
        if (request['source_word_ids'] != expected_request['source_word_ids']
                or request['request_sha256'] != expected_hash):
            raise processing.ProcessingError('The fast detector boundary audit changed request source identity.')
        owned_scenes.extend(request['scene_ids'])
    if owned_scenes != list(range(len(plan['scene_blocks']))):
        raise processing.ProcessingError('The fast detector boundary audit omitted or duplicated a scene.')
    votes, decisions = boundary['word_votes'], audit['word_decisions']
    if (not isinstance(votes, dict) or set(votes) != set(plan['target_word_ids'])
            or any(type(i) is not int or not isinstance(labels, list) or len(labels) != 1
                   or not isinstance(labels[0], str) or labels[0] not in WORD_LABELS for i, labels in votes.items())
            or not isinstance(decisions, dict) or set(decisions) != set(range(count))
            or any(type(i) is not int or not isinstance(label, str) or label not in WORD_LABELS
                   for i, label in decisions.items())):
        raise processing.ProcessingError('The fast detector review audit has incomplete source decisions.')
    expected_decisions = {**plan['defaults'], **{i: labels[0] for i, labels in votes.items()}}
    if decisions != expected_decisions:
        raise processing.ProcessingError('The fast detector review audit contradicts its global or boundary decisions.')
    expected_unresolved = [i for i, label in decisions.items() if label == 'uncertain']
    unresolved = audit['unresolved_word_ids']
    if (boundary['unresolved_word_ids'] != expected_unresolved or not isinstance(unresolved, list)
            or any(type(i) is not int or i not in decisions for i in unresolved)
            or unresolved != sorted(set(unresolved)) or not set(expected_unresolved) <= set(unresolved)
            or any(decisions[i] != 'commercial' or original_words[i]['start'] != original_words[i]['end']
                   for i in set(unresolved) - set(expected_unresolved))):
        raise processing.ProcessingError('The fast detector review audit has invalid unresolved decisions.')

    def fingerprint(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    return {'schema_version': 1, 'analysis_policy': dict(profile), 'transcript_sha256': fingerprint(transcript),
            'detected_cuts_sha256': fingerprint(cuts), 'duration': transcript['duration'], 'audit': audit,
            'requires_manual_review': True, 'automatic_acceptance_validated': False}


def _write_fast_audit(folder, document):
    destination = Path(folder) / 'fast-analysis.json'
    temporary = destination.with_name('.' + destination.name + '.' + uuid.uuid4().hex + '.part')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


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
                if operation == 'process' and not supplied['download_only']:
                    request['transcription_policy'] = self._transcription_policy(selected_settings, self.model)
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
        return {'review_only': review or settings.get('ai_policy') == 'fast',
                'auto_approve_threshold': float(threshold)}

    @staticmethod
    def _transcription_policy(settings, default_model='base'):
        from .config import _validated
        from .batched_transcription import VERSION
        selected = _validated({
            'transcription_model': settings.get('transcription_model') or default_model,
            'transcription_batch_size': settings.get('transcription_batch_size', 1),
            'language': settings.get('language'),
        })
        selected['transcription_profile'] = VERSION if selected['transcription_batch_size'] > 1 else 'cuda-serial-v1'
        return selected

    @classmethod
    def _restore_transcription_policy(cls, snapshot):
        if not isinstance(snapshot, dict):
            raise ValueError('Invalid saved transcription policy')
        current = cls._transcription_policy(snapshot)
        if current != snapshot:
            raise processing.ProcessingError('The speech profile changed since this job was queued. Choose Generate transcript again to use the current profile; saved audio and edits are retained.')
        return current

    @classmethod
    def _detection_policy(cls, settings, detector=None):
        """Snapshot only validated, nonsecret choices and request-profile identity."""
        from .ad_scope import REMOVAL_SCOPE
        policy = settings.get('ai_policy', 'legacy')
        selected_detector = detector or settings.get('detector') or 'auto'
        reasoning = settings.get('ai_reasoning', False)
        address = settings.get('ai_base_url', '')
        model = settings.get('ai_model', '')
        if (policy not in {'legacy', 'verified', 'scene', 'fast'} or selected_detector not in {'auto', 'ai', 'heuristic'}
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
        elif policy == 'fast':
            from .fast_review import POLICY_VERSION, INFERENCE_PROFILE, inference_settings, word_inference_settings
            version = POLICY_VERSION
            encoded = json.dumps({'scenes': inference_settings(), 'words': word_inference_settings()},
                                 sort_keys=True, separators=(',', ':'))
            profile = INFERENCE_PROFILE + '-' + hashlib.sha256(encoded.encode()).hexdigest()
            reasoning = False
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
            if 'transcription_policy' in request and operation == 'process' and not self._option(options, 'download_only', False):
                settings.update(self._restore_transcription_policy(request['transcription_policy']))
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
                batch_size=settings.get('transcription_batch_size', 1),
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
            audits = []
            audit_options = {'audit_sink': audits.append} if profile['ai_policy'] == 'fast' else {}
            cuts = processing.detect_ads(
                transcript, detector=detector, config=settings,
                progress=lambda message: self.progress(episode_id, 'detecting', message),
                should_cancel=cancel.is_set,
                **audit_options,
            )
            cuts = processing.validate_cuts(cuts, duration)
            if profile['ai_policy'] == 'fast':
                cuts = [dict(cut, approved=False) for cut in cuts]
                document = _fast_audit_document(audits, transcript, cuts, profile)
                self._check_cancel(cancel)
                _write_fast_audit(folder, document)
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
        experimental_review = (episode.get('analysis_profile') or {}).get('ai_policy') == 'fast'
        review = no_speech or coverage_review or experimental_review or any(not cut.get('approved') for cut in cuts)
        if automatic and review:
            message = ('No speech detected; review original audio' if no_speech else
                       'Transcript coverage needs review; original audio retained' if coverage_review else
                       'Review and save the experimental fast analysis before exporting; original audio retained' if experimental_review else
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
                   'Review and save the experimental fast analysis before exporting' if experimental_review else
                   'Review suggested cuts' if review else
                   'Approved cuts exported' if has_approved else
                   'No cuts selected; original audio retained')
        self.library.update(episode_id, status='review' if review else 'ready', progress=message)
