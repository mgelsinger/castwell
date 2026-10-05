"""Queue behavior: cancellation, durable retries, and reviewed edit protection."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from castwell import processing
from castwell.jobs import Jobs
from castwell.library import Library


TRANSCRIPT = {
    'duration': 12, 'language': 'en',
    'segments': [{'id': 0, 'start': 0, 'end': 12, 'text': 'A conversation about astronomy.'}],
}
CUT = {'start': 3.0, 'end': 6.0, 'approved': True, 'source': 'manual',
       'confidence': 1.0, 'reason': 'A reviewed advertisement'}


class JobsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.library = Library(self.temporary.name)
        self.jobs = Jobs(self.library, 'base')
        # Wait in test cleanup only, after each test has released its fixtures.
        self.addCleanup(self.jobs.pool.shutdown, wait=True, cancel_futures=True)
        self.addCleanup(self.jobs.shutdown)
        self.probe = patch('castwell.processing.probe_duration', return_value=12).start()
        self.waveform = patch('castwell.processing.waveform', return_value={'duration': 12, 'peaks': [0.4, 0.8]}).start()
        self.addCleanup(patch.stopall)

    def episode(self, identifier='first', *, transcript=None, audio=True, **values):
        self.library.import_entries([{'id': identifier, 'title': identifier.title(),
                                     'media_url': 'https://example.test/episode.wav', 'duration': 99}])
        if audio:
            original = self.library.directory(identifier) / 'original.wav'
            original.write_bytes(b'test audio; decoder is stubbed at its boundary')
            values['audio'] = str(original)
        if transcript is not None:
            values['transcript'] = deepcopy(transcript)
        if values:
            self.library.update(identifier, **values)
        return identifier

    def wait(self, identifier='first'):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with self.jobs.lock:
                if identifier not in self.jobs.pending:
                    return self.library.get(identifier)
            time.sleep(0.005)
        self.fail('The queue did not settle')

    def test_download_only_needs_no_transcription_model_or_classifier(self):
        identifier = self.episode()
        with patch('castwell.processing.transcribe') as transcribe, patch('castwell.processing.detect_ads') as detect:
            self.assertTrue(self.jobs.submit(identifier, 'process', {'download_only': True}))
            episode = self.wait()
        self.assertEqual(episode['status'], 'downloaded')
        self.assertEqual(episode['duration'], 12)
        self.assertEqual(episode['waveform']['peaks'], [0.4, 0.8])
        transcribe.assert_not_called()
        detect.assert_not_called()

    def test_existing_audio_and_imported_transcript_are_reused_and_reconciled(self):
        imported = dict(TRANSCRIPT, duration=99)
        identifier = self.episode(transcript=imported)
        with patch('castwell.jobs.download_media') as download, patch('castwell.processing.transcribe') as transcribe:
            self.jobs.submit(identifier, 'process', {'remove_ads': False})
            episode = self.wait()
        self.assertEqual(episode['status'], 'ready', episode['error'])
        self.assertEqual(episode['transcript']['duration'], 12)
        download.assert_not_called()
        transcribe.assert_not_called()

    def test_cleared_cuts_and_no_ad_results_do_not_trigger_redetection(self):
        identifier = self.episode(transcript=TRANSCRIPT, analysis_done=True)
        with patch('castwell.processing.detect_ads') as detect:
            self.jobs.submit(identifier, 'process')
            episode = self.wait()
        detect.assert_not_called()
        self.assertEqual(episode['cuts'], [])
        self.assertEqual(episode['status'], 'ready')
        self.assertNotIn('No ads detected', episode['progress'])

    def test_manual_cuts_are_kept_even_without_a_previous_detector_run(self):
        identifier = self.episode(transcript=TRANSCRIPT, cuts=[dict(CUT, approved=False)], analysis_done=False)
        with patch('castwell.processing.detect_ads') as detect:
            self.jobs.submit(identifier, 'process')
            episode = self.wait()
        detect.assert_not_called()
        self.assertEqual(episode['cuts'][0]['source'], 'manual')
        self.assertEqual(episode['status'], 'review')

    def test_explicit_redetection_saves_old_cuts_and_cleaned_duration(self):
        identifier = self.episode(transcript=TRANSCRIPT, analysis_done=True, cuts=[dict(CUT, approved=False)])
        with patch('castwell.processing.detect_ads', return_value=[CUT]), \
             patch('castwell.processing.render_audio', return_value={'duration': 9, 'removed_seconds': 3}), \
             patch.object(self.library, 'save_revision', wraps=self.library.save_revision) as revision:
            self.jobs.submit(identifier, 'process', {'redetect': True})
            episode = self.wait()
        self.assertEqual(episode['status'], 'ready', episode['error'])
        self.assertEqual(episode['cleaned_duration'], 9)
        self.assertEqual(episode['removed_seconds'], 3)
        self.assertTrue(episode['analysis_done'])
        self.assertEqual(revision.call_args.args[1], [dict(CUT, approved=False)])

    def test_settings_are_passed_to_the_transcriber_and_detector(self):
        identifier = self.episode()
        settings = {'transcription_model': 'small', 'language': 'fr', 'detector': 'ai',
                    'ai_model': 'configured-model', 'ai_base_url': 'https://example.test/v1',
                    'review_only': True, 'auto_approve_threshold': 0.97}
        self.jobs.settings_getter = lambda: settings
        with patch('castwell.processing.transcribe', return_value=TRANSCRIPT) as transcribe, \
             patch('castwell.processing.detect_ads', return_value=[]) as detect:
            self.jobs.submit(identifier, 'process', {'detector': None})
            episode = self.wait()
        self.assertEqual(episode['status'], 'ready', episode['error'])
        self.assertEqual(transcribe.call_args.kwargs['model'], 'small')
        self.assertEqual(transcribe.call_args.kwargs['language'], 'fr')
        self.assertEqual(detect.call_args.kwargs['detector'], 'ai')
        self.assertEqual(detect.call_args.kwargs['config'], settings)

    def test_silent_transcript_never_claims_successful_ad_detection(self):
        identifier = self.episode(transcript={'duration': 12, 'segments': []})
        with patch('castwell.processing.transcribe') as transcribe, patch('castwell.processing.detect_ads') as detect:
            self.jobs.submit(identifier, 'process')
            episode = self.wait()
        transcribe.assert_not_called()
        detect.assert_not_called()
        self.assertEqual(episode['status'], 'review')
        self.assertIn('No speech detected', episode['progress'])
        self.assertFalse(episode['analysis_done'])

    def test_failed_detection_retries_from_saved_transcript(self):
        identifier = self.episode()
        with patch('castwell.processing.transcribe', return_value=TRANSCRIPT) as transcribe, \
             patch('castwell.processing.detect_ads', side_effect=RuntimeError('Classifier unavailable')):
            self.jobs.submit(identifier, 'process')
            failed = self.wait()
        self.assertEqual(failed['status'], 'error')
        self.assertIn('detecting', failed['progress'])
        self.assertIsNotNone(failed['transcript'])
        transcribe.assert_called_once()
        with patch('castwell.processing.transcribe') as transcribe, patch('castwell.processing.detect_ads', return_value=[]):
            self.jobs.submit(identifier, 'process')
            episode = self.wait()
        self.assertEqual(episode['status'], 'ready', episode['error'])
        self.assertIsNone(episode['error'])
        transcribe.assert_not_called()

    def test_cancelled_active_job_retains_completed_transcript_and_can_resume(self):
        identifier = self.episode()
        entered, release = threading.Event(), threading.Event()

        def transcribe(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test did not release transcription')
            return TRANSCRIPT

        with patch('castwell.processing.transcribe', side_effect=transcribe), patch('castwell.processing.detect_ads') as detect:
            try:
                self.jobs.submit(identifier, 'process')
                self.assertTrue(entered.wait(2))
                self.assertTrue(self.jobs.cancel(identifier))
                self.assertFalse(self.jobs.submit(identifier, 'process'))
            finally:
                release.set()
            episode = self.wait()
        self.assertEqual(episode['status'], 'cancelled')
        self.assertIsNotNone(episode['transcript'])
        self.assertTrue(Path(episode['audio']).is_file())
        detect.assert_not_called()
        self.assertFalse(self.jobs.cancel(identifier))
        with patch('castwell.processing.transcribe') as transcribe:
            self.assertTrue(self.jobs.submit(identifier, 'process', {'remove_ads': False}))
            episode = self.wait()
        self.assertEqual(episode['status'], 'ready')
        transcribe.assert_not_called()

    def test_queued_cancel_cleans_pending_and_preserves_queue_order(self):
        first = self.episode('first')
        second = self.episode('second')
        third = self.episode('third')
        entered, release = threading.Event(), threading.Event()

        def transcribe(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test did not release transcription')
            return TRANSCRIPT

        with patch('castwell.processing.transcribe', side_effect=transcribe) as transcribe:
            try:
                self.jobs.submit(first, 'process', {'remove_ads': False})
                self.assertTrue(entered.wait(2))
                self.jobs.submit(second, 'process', {'remove_ads': False})
                self.jobs.submit(third, 'process', {'download_only': True})
                self.assertEqual([item['episode_id'] for item in self.jobs.snapshot()], [first, second, third])
                self.assertTrue(self.jobs.cancel(second))
                self.assertNotIn(second, self.jobs.pending)
                self.assertEqual(self.library.get(second)['status'], 'cancelled')
                snapshot = self.jobs.snapshot()
                self.assertEqual([(item['episode_id'], item['position']) for item in snapshot], [(first, 1), (third, 2)])
            finally:
                release.set()
            self.wait(first)
            self.wait(third)
        transcribe.assert_called_once()
        self.assertEqual(self.jobs.snapshot(), [])

    def test_shutdown_cancels_queued_and_current_jobs_without_waiting(self):
        first = self.episode('first')
        second = self.episode('second')
        entered, release = threading.Event(), threading.Event()

        def transcribe(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Test did not release transcription')
            if kwargs['should_cancel']():
                raise processing.ProcessingCancelled('Cancelled')
            return TRANSCRIPT

        with patch('castwell.processing.transcribe', side_effect=transcribe):
            try:
                self.jobs.submit(first, 'process')
                self.assertTrue(entered.wait(2))
                self.jobs.submit(second, 'process')
                started = time.monotonic()
                self.jobs.shutdown()
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertFalse(self.jobs.submit(second, 'process'))
                self.assertEqual(self.library.get(second)['status'], 'cancelled')
            finally:
                release.set()
            episode = self.wait(first)
        self.assertEqual(episode['status'], 'cancelled')
        self.assertEqual(self.jobs.snapshot(), [])

    def test_download_failure_redacts_credential_bearing_messages(self):
        identifier = self.episode(audio=False)
        with patch('castwell.jobs.download_media', side_effect=RuntimeError('https://user:secret@example.test/audio')):
            self.jobs.submit(identifier, 'process')
            episode = self.wait()
        self.assertEqual(episode['status'], 'error')
        self.assertIn('downloading', episode['progress'])
        self.assertNotIn('secret', episode['error'])

    def test_replacement_audio_invalidates_old_timestamps_and_keeps_review_history(self):
        identifier = self.episode(transcript=TRANSCRIPT, cuts=[CUT], analysis_done=True,
                                  waveform={'duration': 12, 'peaks': [1]}, cleaned='old-cleaned.mp3',
                                  position=8, played=True)
        Path(self.library.get(identifier)['audio']).unlink()

        def download(url, destination, **kwargs):
            destination.write_bytes(b'a new recording with different dynamically inserted ads')
            return destination

        fresh = dict(TRANSCRIPT, segments=[dict(TRANSCRIPT['segments'][0], text='New recording')])
        with patch('castwell.jobs.download_media', side_effect=download), \
             patch('castwell.processing.transcribe', return_value=fresh) as transcribe, \
             patch('castwell.processing.detect_ads', return_value=[]) as detect:
            self.jobs.submit(identifier, 'process')
            episode = self.wait()
        self.assertEqual(episode['status'], 'ready', episode['error'])
        self.assertEqual(episode['transcript']['segments'][0]['text'], 'New recording')
        self.assertEqual(episode['cuts'], [])
        self.assertIsNone(episode['cleaned'])
        self.assertEqual(episode['position'], 0)
        self.assertFalse(episode['played'])
        transcribe.assert_called_once()
        detect.assert_called_once()
        self.waveform.assert_called_once()
        self.assertEqual(self.library.revisions(identifier)[0]['cuts'], [CUT])
        backup = next(self.library.directory(identifier).glob('previous-transcript-*.json'))
        self.assertEqual(json.loads(backup.read_text()), TRANSCRIPT)

    def test_cancel_at_replacement_publication_cannot_reuse_stale_transcript(self):
        identifier = self.episode(transcript=TRANSCRIPT, cuts=[CUT], analysis_done=True)
        Path(self.library.get(identifier)['audio']).unlink()

        def download(url, destination, **kwargs):
            destination.write_bytes(b'new recording')
            self.jobs.cancel(identifier)
            kwargs['progress']('Download complete')
            return destination

        with patch('castwell.jobs.download_media', side_effect=download):
            self.jobs.submit(identifier, 'process')
            episode = self.wait()
        self.assertEqual(episode['status'], 'cancelled')
        self.assertIsNone(episode['transcript'])
        self.assertEqual(episode['cuts'], [])
        self.assertFalse(episode['analysis_done'])

    def test_first_download_preserves_imported_transcript(self):
        identifier = self.episode(audio=False, transcript=TRANSCRIPT)

        def download(url, destination, **kwargs):
            destination.write_bytes(b'first recording')
            return destination

        with patch('castwell.jobs.download_media', side_effect=download), patch('castwell.processing.transcribe') as transcribe:
            self.jobs.submit(identifier, 'process', {'remove_ads': False})
            episode = self.wait()
        self.assertEqual(episode['status'], 'ready', episode['error'])
        transcribe.assert_not_called()
        self.assertEqual(episode['transcript']['segments'], TRANSCRIPT['segments'])

    def test_uninterruptible_model_load_cannot_hold_process_exit(self):
        # Some native model loaders cannot check cancellation until they return.
        # Exercise an actual interpreter exit, not just shutdown()'s return time.
        script = '''
import tempfile
import threading
from castwell.jobs import Jobs
from castwell.library import Library
library = Library(tempfile.mkdtemp())
library.import_entries([{'id': 'episode', 'title': 'Example'}])
jobs = Jobs(library, 'base')
started = threading.Event()
def blocked_model(*args, **kwargs):
    started.set()
    threading.Event().wait(60)
jobs._process = blocked_model
jobs.submit('episode', 'process')
assert started.wait(2)
jobs.shutdown()
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=4)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
