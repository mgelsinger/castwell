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


def fast_audit_fixture(transcript, *, unresolved=False):
    from castwell import fast_review
    from castwell.ad_scope import REMOVAL_SCOPE
    from castwell.episode_boundaries import build_boundary_plan, refine_boundaries
    from castwell.episode_omissions import omission_guard
    units = processing._classification_transcript(transcript)['segments']
    global_decisions = {unit['id']: 'uncertain' if unresolved else 'editorial' for unit in units}
    plan = build_boundary_plan(units, global_decisions)
    decisions, boundary_audit = refine_boundaries(plan,
        request=lambda request: {i: 'uncertain' for i in range(len(request['units']))}, fits=lambda request: True)
    return {'policy_version': fast_review.POLICY_VERSION, 'removal_scope': REMOVAL_SCOPE,
            'inference_profile': fast_review.INFERENCE_PROFILE,
            'global_decisions': global_decisions, 'boundary_audit': boundary_audit,
            'omission_guard': omission_guard(units, global_decisions),
            'sweep_requests': [{'core_ids': list(global_decisions), 'source_ids': list(global_decisions)}],
            'sweep_votes': {i: [label] for i, label in global_decisions.items()},
            'word_decisions': decisions, 'unresolved_word_ids': list(decisions) if unresolved else [],
            'source_word_count': len(plan['words']), 'requires_review': True, 'automatic_acceptance_validated': False}


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
        supplied = detect.call_args.kwargs['config']
        self.assertEqual({key: supplied[key] for key in settings}, settings)
        self.assertEqual(supplied['ai_policy'], 'legacy')

    def test_queued_speech_model_language_and_batch_size_survive_settings_changes_and_retry(self):
        identifier = self.episode()
        settings = {'transcription_model': 'small', 'language': 'fr', 'transcription_batch_size': 8}
        self.jobs.settings_getter = lambda: dict(settings)
        release = threading.Event()
        self.jobs.pool.submit(lambda: release.wait(3))
        with patch('castwell.processing.transcribe', side_effect=processing.ProcessingError('fixture failure')) as transcribe:
            try:
                self.jobs.submit(identifier, 'process', {'remove_ads': False})
                expected = self.library.get(identifier)['pending_job']['transcription_policy']
                self.assertEqual(expected['transcription_batch_size'], 8)
                settings.update(transcription_model='large-v3', language='en', transcription_batch_size=1)
            finally:
                release.set()
            failed = self.wait(identifier)
        self.assertEqual(failed['pending_job']['transcription_policy'], expected)
        self.assertEqual(transcribe.call_args.kwargs['model'], 'small')
        self.assertEqual(transcribe.call_args.kwargs['language'], 'fr')
        self.assertEqual(transcribe.call_args.kwargs['batch_size'], 8)
        with patch('castwell.processing.transcribe', return_value=TRANSCRIPT) as transcribe:
            self.jobs.submit(identifier, 'process')
            finished = self.wait(identifier)
        self.assertEqual(finished['status'], 'ready', finished['error'])
        self.assertEqual(transcribe.call_args.kwargs['batch_size'], 8)
        self.assertEqual(transcribe.call_args.kwargs['model'], 'small')

    def test_fast_analysis_stays_manual_even_when_empty_until_decisions_saved(self):
        settings = {'ai_policy': 'fast', 'review_only': False, 'ai_reasoning': True,
                    'ai_base_url': 'http://127.0.0.1:8081/v1', 'ai_model': 'fixture', 'detector': 'ai'}
        self.jobs.settings_getter = lambda: dict(settings)
        aligned = deepcopy(TRANSCRIPT)
        aligned['segments'][0]['words'] = [{'word': 'Speech.', 'start': 0., 'end': 12.}]
        for name, proposed in (('empty', []), ('suggestions', [dict(CUT, source='ai')])):
            with self.subTest(name=name):
                identifier = self.episode(name, transcript=aligned)
                def detected(transcript, **options):
                    options['audit_sink'](fast_audit_fixture(transcript))
                    return proposed
                with patch('castwell.processing.detect_ads', side_effect=detected) as detect, \
                     patch('castwell.processing.render_audio') as render:
                    self.jobs.submit(identifier, 'process')
                    episode = self.wait(identifier)
                self.assertEqual(episode['status'], 'review', episode['error'])
                self.assertTrue(detect.call_args.kwargs['config']['review_only'])
                self.assertFalse(detect.call_args.kwargs['config']['ai_reasoning'])
                self.assertTrue(all(not cut['approved'] for cut in episode['cuts']))
                self.assertFalse(self.library.public(episode)['export_ready'])
                audit_path = self.library.directory(identifier) / 'fast-analysis.json'
                self.assertTrue(audit_path.is_file())
                render.assert_not_called()
                self.jobs.render(identifier)
                self.assertEqual(self.library.get(identifier)['status'], 'review')
                # Saving an explicit empty decision clears the experimental
                # analysis profile, as it does for other reviewed detector cuts.
                self.library.update(identifier, cuts=[])
                self.jobs.render(identifier)
                reviewed = self.library.get(identifier)
                self.assertIsNone(reviewed['analysis_profile'])
                self.assertTrue(self.library.public(reviewed)['export_ready'])

    def test_fast_empty_unresolved_audit_is_saved_and_failure_preserves_prior_evidence(self):
        aligned = deepcopy(TRANSCRIPT)
        aligned['segments'][0]['words'] = [{'word': 'Uncertain.', 'start': 0., 'end': 12.}]
        identifier = self.episode(transcript=aligned)
        self.jobs.settings_getter = lambda: {'ai_policy': 'fast', 'ai_model': 'fixture',
                                             'ai_base_url': 'http://127.0.0.1:8081/v1', 'ai_key': 'private-fixture-key'}
        def detected(transcript, **options):
            options['audit_sink'](fast_audit_fixture(transcript, unresolved=True))
            return []
        with patch('castwell.processing.detect_ads', side_effect=detected):
            self.jobs.submit(identifier, 'process')
            finished = self.wait(identifier)
        self.assertEqual(finished['status'], 'review', finished['error'])
        sidecar = self.library.directory(identifier) / 'fast-analysis.json'
        original = sidecar.read_bytes()
        saved = json.loads(original)
        self.assertEqual(saved['audit']['unresolved_word_ids'], [0])
        self.assertEqual(saved['audit']['word_decisions'], {'0': 'uncertain'})
        self.assertNotIn('private-fixture-key', original.decode())
        with patch('castwell.processing.detect_ads', return_value=[]):
            self.jobs.submit(identifier, 'process', {'redetect': True})
            failed = self.wait(identifier)
        self.assertEqual(failed['status'], 'error')
        self.assertIn('complete review audit', failed['error'])
        self.assertEqual(sidecar.read_bytes(), original)
        self.assertEqual(failed['analysis_profile'], finished['analysis_profile'])
        self.assertFalse(list(sidecar.parent.glob('.fast-analysis*.part')))

    def test_fast_retry_profile_includes_both_request_profiles(self):
        from castwell import fast_review
        snapshot = Jobs._detection_policy({'ai_policy': 'fast'})
        with patch('castwell.fast_review.word_inference_settings', return_value={**fast_review.word_inference_settings(), 'max_tokens': 99}):
            with self.assertRaisesRegex(processing.ProcessingError, 'profile changed'):
                Jobs._restore_detection_policy(snapshot)

    def test_fast_audit_rejects_missing_sweep_votes_word_votes_and_contradictions(self):
        from castwell.jobs import _fast_audit_document
        aligned = deepcopy(TRANSCRIPT)
        aligned['segments'][0]['words'] = [{'word': 'Speech.', 'start': 0., 'end': 12.}]
        profile = Jobs._detection_policy({'ai_policy': 'fast'})
        complete = fast_audit_fixture(aligned, unresolved=True)
        for label, mutation in (
            ('missing global source', lambda audit: audit.update(global_decisions={})),
            ('missing omission guard', lambda audit: audit.pop('omission_guard')),
            ('changed omission guard', lambda audit: audit['omission_guard'].update(version='changed-guard')),
            ('missing sweep requests', lambda audit: audit.pop('sweep_requests')),
            ('missing sweep votes', lambda audit: audit.pop('sweep_votes')),
            ('missing all sweep evidence', lambda audit: (audit.pop('sweep_requests'), audit.pop('sweep_votes'))),
            ('missing word votes', lambda audit: audit['boundary_audit'].update(word_votes={})),
            ('contradictory decision', lambda audit: audit['word_decisions'].update({0: 'editorial'})),
            ('changed source identity', lambda audit: audit['boundary_audit']['requests'][0].update(request_sha256='0' * 64)),
            ('duplicated scene', lambda audit: audit['boundary_audit']['requests'].append(deepcopy(audit['boundary_audit']['requests'][0]))),
            ('missing sweep core', lambda audit: audit.update(sweep_requests=[])),
            ('wrong sweep vote count', lambda audit: audit['sweep_votes'].update({0: ['uncertain', 'uncertain']})),
            ('contradictory sweep', lambda audit: audit['sweep_votes'].update({0: ['commercial']})),
        ):
            broken = deepcopy(complete)
            mutation(broken)
            with self.subTest(label=label), self.assertRaises((processing.ProcessingError, ValueError)):
                _fast_audit_document([broken], aligned, [], profile)

    def test_fast_empty_result_requires_complete_episode_scan_evidence(self):
        from castwell.jobs import _fast_audit_document
        aligned = deepcopy(TRANSCRIPT)
        aligned['segments'][0]['words'] = [{'word': 'Editorial.', 'start': 0., 'end': 12.}]
        profile = Jobs._detection_policy({'ai_policy': 'fast'})
        audit = fast_audit_fixture(aligned)
        self.assertTrue(_fast_audit_document([audit], aligned, [], profile)['requires_manual_review'])
        audit.pop('sweep_requests')
        audit.pop('sweep_votes')
        with self.assertRaisesRegex(processing.ProcessingError, 'complete review audit'):
            _fast_audit_document([audit], aligned, [], profile)

    def test_fast_audit_cannot_hide_editorial_sponsor_cues_or_accept_a_flagged_guard(self):
        from castwell.jobs import _fast_audit_document
        aligned = deepcopy(TRANSCRIPT)
        text = 'This episode is sponsored by Example.'
        aligned['segments'][0].update(text=text, words=[{'word': text, 'start': 0., 'end': 12.}])
        profile = Jobs._detection_policy({'ai_policy': 'fast'})
        audit = fast_audit_fixture(aligned)
        self.assertTrue(audit['omission_guard']['guard_requires_review'])
        with self.assertRaisesRegex(processing.ProcessingError, 'omission guard'):
            _fast_audit_document([audit], aligned, [], profile)
        audit['omission_guard'].update(flagged_unit_ids=[], cue_clusters=[], guard_requires_review=False)
        with self.assertRaisesRegex(processing.ProcessingError, 'omission guard'):
            _fast_audit_document([audit], aligned, [], profile)

    def test_fast_profile_cannot_be_exported_after_keep_sets_ready(self):
        profile = Jobs._detection_policy({'ai_policy': 'fast'})
        identifier = self.episode(transcript=TRANSCRIPT, status='ready', analysis_done=True,
                                  analysis_profile=profile)
        episode = self.library.get(identifier)
        removed = self.library.public(dict(episode, ad_mode='remove'))
        self.assertFalse(removed['export_ready'])
        self.assertEqual(removed['status'], 'review')
        kept = self.library.public(dict(episode, ad_mode='keep'))
        self.assertTrue(kept['export_ready'])
        self.assertEqual(kept['preferred_version'], 'original')

    def test_changed_speech_profile_is_rejected_without_running_inference(self):
        snapshot = Jobs._transcription_policy({'transcription_batch_size': 8})
        snapshot['transcription_profile'] = 'an-older-batching-policy'
        with self.assertRaisesRegex(processing.ProcessingError, 'speech profile changed'):
            Jobs._restore_transcription_policy(snapshot)

    def test_queue_freezes_detector_endpoint_model_and_profile_but_not_key(self):
        identifier = self.episode(transcript=TRANSCRIPT)
        settings = {'ai_policy': 'verified', 'ai_reasoning': False, 'detector': 'ai',
                    'ai_base_url': 'http://127.0.0.1:8081/v1', 'ai_model': 'original-model',
                    'ai_key': 'first-private-key', 'review_only': True}
        self.jobs.settings_getter = lambda: dict(settings)
        release = threading.Event()
        self.jobs.pool.submit(lambda: release.wait(3))
        with patch('castwell.processing.detect_ads', return_value=[]) as detect:
            try:
                self.jobs.submit(identifier, 'process')
                snapshot = self.library.get(identifier)['pending_job']['detection_policy']
                self.assertNotIn('first-private-key', json.dumps(snapshot))
                settings.update(ai_policy='legacy', ai_reasoning=True, detector='heuristic',
                                ai_base_url='http://127.0.0.1:9999/v1', ai_model='replacement-model',
                                ai_key='current-private-key')
            finally:
                release.set()
            episode = self.wait()
        self.assertEqual(episode['status'], 'ready', episode['error'])
        selected = detect.call_args.kwargs['config']
        for field in ('ai_policy', 'ai_reasoning', 'detector', 'ai_base_url', 'ai_model', 'inference_profile'):
            self.assertEqual(selected[field], snapshot[field])
        self.assertEqual(selected['ai_key'], 'current-private-key')
        self.assertEqual(episode['analysis_profile'], snapshot)
        self.assertNotIn('current-private-key', json.dumps(episode['analysis_profile']))

    def test_retry_refuses_changed_detector_implementation_and_preserves_edits(self):
        from castwell import ad_review
        identifier = self.episode(transcript=TRANSCRIPT, cuts=[CUT], analysis_done=True)
        self.jobs.settings_getter = lambda: {'ai_policy': 'verified', 'review_only': True}
        with patch('castwell.processing.detect_ads', side_effect=processing.ProcessingError('Unavailable')):
            self.jobs.submit(identifier, 'process', {'redetect': True})
            failed = self.wait()
        snapshot = failed['pending_job']['detection_policy']
        with patch.object(ad_review, 'POLICY_VERSION', 'different-policy'), \
             patch('castwell.processing.detect_ads') as detect:
            self.jobs.submit(identifier, 'process')
            failed = self.wait()
        self.assertEqual(failed['status'], 'error')
        self.assertIn('profile changed', failed['error'])
        self.assertEqual(failed['cuts'], [CUT])
        self.assertEqual(failed['pending_job']['detection_policy'], snapshot)
        detect.assert_not_called()

    def test_scene_snapshot_includes_both_review_and_endpoint_profiles(self):
        from castwell import scene_review, scene_edges, scene_pipeline
        snapshot = Jobs._detection_policy({'ai_policy': 'scene', 'review_only': True})
        self.assertEqual(snapshot['policy_version'], scene_pipeline.POLICY_VERSION + '+' + scene_review.POLICY_VERSION + '+' + scene_edges.POLICY_VERSION)
        self.assertEqual(snapshot['inference_profile'], scene_review.INFERENCE_PROFILE + '+' + scene_edges.INFERENCE_PROFILE)
        with patch.object(scene_edges, 'INFERENCE_PROFILE', 'changed-endpoint-profile'):
            with self.assertRaisesRegex(processing.ProcessingError, 'profile changed'):
                Jobs._restore_detection_policy(snapshot)

    def test_all_detector_snapshots_include_canonical_removal_scope(self):
        from castwell.ad_scope import REMOVAL_SCOPE
        for policy in ('legacy', 'verified', 'scene'):
            for detector in ('auto', 'ai', 'heuristic'):
                with self.subTest(policy=policy, detector=detector):
                    snapshot = Jobs._detection_policy({'ai_policy': policy, 'detector': detector})
                    self.assertEqual(snapshot['removal_scope'], REMOVAL_SCOPE)
                    self.assertEqual(Jobs._restore_detection_policy(snapshot), snapshot)
                    old_snapshot = {key: value for key, value in snapshot.items() if key != 'removal_scope'}
                    with self.assertRaisesRegex(processing.ProcessingError, 'profile changed'):
                        Jobs._restore_detection_policy(old_snapshot)
        self.assertEqual(Jobs._detection_policy({'ai_policy': 'legacy', 'detector': 'ai'})['policy_version'],
                         processing.LEGACY_AI_POLICY_VERSION)

    def test_old_known_legacy_analysis_cannot_be_reused_under_new_removal_scope(self):
        settings = {'ai_policy': 'legacy', 'detector': 'ai', 'ai_model': 'fixture',
                    'ai_base_url': 'http://127.0.0.1:8081/v1'}
        old_profile = Jobs._detection_policy(settings)
        old_profile.pop('removal_scope')
        old_profile['policy_version'] = 'legacy-unversioned'
        old_cuts = [dict(CUT, source='ai', reason='Saved under the previous broader removal scope')]
        identifier = self.episode(transcript=TRANSCRIPT, cuts=old_cuts, analysis_done=True,
                                  analysis_profile=old_profile)
        self.jobs.settings_getter = lambda: dict(settings)
        with patch('castwell.processing.detect_ads') as detect, patch('castwell.processing.render_audio') as render:
            self.jobs.submit(identifier, 'process')
            episode = self.wait()
        self.assertEqual(episode['status'], 'error')
        self.assertIn('Find ads again', episode['error'])
        self.assertEqual(episode['cuts'], old_cuts)
        self.assertEqual(episode['analysis_profile'], old_profile)
        detect.assert_not_called()
        render.assert_not_called()

    def test_empty_scene_cue_fallback_cannot_complete_even_with_clear_coverage(self):
        identifier = self.episode(transcript=TRANSCRIPT)
        self.jobs.settings_getter = lambda: {'ai_policy': 'scene', 'ai_base_url': '', 'ai_model': '',
                                             'ai_key': '', 'review_only': False}
        with patch('castwell.processing.render_audio') as render:
            self.jobs.submit(identifier, 'process')
            episode = self.wait()
        self.assertEqual(episode['status'], 'error')
        self.assertIn('Cue-only detection cannot confirm', episode['error'])
        self.assertFalse(episode['analysis_done'])
        self.assertFalse(episode['transcript_review'])
        self.assertEqual(episode['cuts'], [])
        self.assertIsNone(episode['cleaned'])
        self.assertTrue(Path(episode['audio']).is_file())
        render.assert_not_called()

    def test_scene_provider_failure_cannot_complete_after_listener_coverage_acknowledgement(self):
        source = deepcopy(TRANSCRIPT)
        source['quality'] = {'requires_review': True, 'warning': 'Review the original recording.'}
        identifier = self.episode(transcript=source)
        token = self.library.coverage_review_token(self.library.get(identifier))
        self.library.acknowledge_coverage(identifier, token)
        self.jobs.settings_getter = lambda: {'ai_policy': 'scene', 'ai_base_url': 'http://127.0.0.1:8081/v1',
                                             'ai_model': 'fixture', 'ai_key': '', 'review_only': False}
        with patch('castwell.scene_pipeline.classify_scene_pipeline',
                   side_effect=processing.ProcessingError('Scene provider failed')), \
             patch('castwell.processing.render_audio') as render:
            self.jobs.submit(identifier, 'process')
            episode = self.wait()
        self.assertEqual(episode['status'], 'error')
        self.assertIn('Scene provider failed', episode['error'])
        self.assertFalse(episode['analysis_done'])
        self.assertFalse(episode['transcript_review'])
        self.assertTrue(self.library.coverage_review_current(episode))
        self.assertEqual(episode['cuts'], [])
        self.assertIsNone(episode['cleaned'])
        render.assert_not_called()

    def test_known_analysis_requires_explicit_redetection_after_model_change(self):
        identifier = self.episode(transcript=TRANSCRIPT)
        settings = {'ai_policy': 'verified', 'ai_model': 'first-model', 'review_only': True}
        self.jobs.settings_getter = lambda: dict(settings)
        with patch('castwell.processing.detect_ads', return_value=[]) as detect:
            self.jobs.submit(identifier, 'process')
            finished = self.wait()
            first_profile = finished['analysis_profile']
            self.jobs.submit(identifier, 'process')
            self.wait()
            self.assertEqual(detect.call_count, 1)
            settings['ai_model'] = 'second-model'
            self.jobs.submit(identifier, 'process')
            failed = self.wait()
            self.assertEqual(failed['status'], 'error')
            self.assertIn('Find ads again', failed['error'])
            self.assertEqual(failed['analysis_profile'], first_profile)
            self.assertEqual(detect.call_count, 1)
            self.jobs.submit(identifier, 'process', {'redetect': True})
            finished = self.wait()
        self.assertEqual(finished['status'], 'ready', finished['error'])
        self.assertEqual(finished['analysis_profile']['ai_model'], 'second-model')
        self.assertEqual(detect.call_count, 2)

    def test_coverage_exception_skips_automatic_render_but_allows_manual_preview(self):
        transcript = {'duration': 12, 'segments': [
            {'id': 0, 'start': 0, 'end': 2, 'text': 'Opening.'},
            {'id': 1, 'start': 7, 'end': 12, 'text': 'Return.'}]}
        identifier = self.episode(transcript=transcript, cuts=[CUT], analysis_done=True)
        with patch('castwell.processing.detect_ads') as detect, \
             patch('castwell.processing.render_audio') as render:
            self.jobs.submit(identifier, 'process')
            finished = self.wait()
        self.assertEqual(finished['status'], 'review')
        self.assertIn('coverage', finished['progress'])
        self.assertTrue(finished['transcript_review'])
        self.assertFalse(self.library.public(finished)['export_ready'])
        render.assert_not_called()
        detect.assert_not_called()
        def manual_render(audio, output, cuts, **options):
            output.write_bytes(b'manual edited preview')
            return {'duration': 9, 'removed_seconds': 3}
        with patch('castwell.processing.render_audio', side_effect=manual_render):
            self.jobs.submit(identifier, 'render')
            finished = self.wait()
        self.assertEqual(finished['status'], 'review')
        self.assertTrue(Path(finished['cleaned']).is_file())
        self.assertEqual(self.library.public(finished)['preferred_version'], 'original')
        self.assertFalse(self.library.public(finished)['export_ready'])
        self.library.set_podcast_mode([self.library.get(identifier)['podcast_id']], 'keep')
        with patch('castwell.processing.detect_ads') as detect:
            self.jobs.submit(identifier, 'process', {'remove_ads': False})
            kept = self.wait()
        self.assertEqual(kept['status'], 'ready', kept['error'])
        detect.assert_not_called()

    def test_keep_podcast_accepts_empty_transcript_without_removal_coverage_gate(self):
        identifier = self.episode(transcript={'duration': 12, 'segments': []})
        self.library.set_podcast_mode([self.library.get(identifier)['podcast_id']], 'keep')
        with patch('castwell.processing.detect_ads') as detect, \
             patch('castwell.processing.render_audio') as render:
            self.jobs.submit(identifier, 'process')
            kept = self.wait()
        public = self.library.public(kept)
        self.assertEqual(kept['status'], 'ready', kept['error'])
        self.assertTrue(kept['transcript_review'])
        self.assertTrue(public['export_ready'])
        self.assertEqual(public['preferred_version'], 'original')
        detect.assert_not_called()
        render.assert_not_called()

    @staticmethod
    def policy_candidate(transcript, detector, *, config, **kwargs):
        return [dict(CUT, source='ai', confidence=.95,
                     approved=not config['review_only'] and .95 >= config['auto_approve_threshold'])]

    def test_queued_jobs_keep_review_and_threshold_despite_later_settings_changes(self):
        for name, review, threshold in (('review', True, .90), ('threshold', False, .97)):
            with self.subTest(control=name):
                identifier = self.episode(name, transcript=TRANSCRIPT)
                settings = {'review_only': review, 'auto_approve_threshold': threshold,
                            'ai_key': 'private-test-key', 'ai_base_url': 'https://example.test/v1'}
                self.jobs.settings_getter = lambda: dict(settings)
                release = threading.Event()
                self.jobs.pool.submit(lambda: release.wait(3))
                with patch('castwell.processing.detect_ads', side_effect=self.policy_candidate) as detect, \
                     patch('castwell.processing.render_audio') as render:
                    try:
                        self.jobs.submit(identifier, 'process')
                        queued = self.library.get(identifier)
                        self.assertEqual(queued['pending_job']['approval_policy'],
                                         {'review_only': review, 'auto_approve_threshold': threshold})
                        self.assertNotIn('private-test-key', json.dumps(queued['pending_job']))
                        self.assertEqual(queued['pending_job']['detection_policy']['ai_base_url'], 'https://example.test/v1')
                        settings.update(review_only=False, auto_approve_threshold=.50)
                    finally:
                        release.set()
                    finished = self.wait(identifier)
                self.assertEqual(finished['status'], 'review', finished['error'])
                self.assertFalse(finished['cuts'][0]['approved'])
                self.assertEqual(detect.call_args.kwargs['config']['auto_approve_threshold'], threshold)
                render.assert_not_called()

    def test_failed_job_preserves_approval_policy_after_reopening_and_retry(self):
        identifier = self.episode(transcript=TRANSCRIPT)
        settings = {'review_only': True, 'auto_approve_threshold': .97, 'ai_model': 'before'}
        self.jobs.settings_getter = lambda: dict(settings)
        with patch('castwell.processing.detect_ads', side_effect=processing.ProcessingError('Unavailable')):
            self.jobs.submit(identifier, 'process')
            failed = self.wait()
        expected = {'review_only': True, 'auto_approve_threshold': .97}
        self.assertEqual(failed['pending_job']['approval_policy'], expected)
        self.jobs.shutdown()
        self.jobs.pool.shutdown(wait=True)
        self.library = Library(self.library.root)
        self.library.recover()
        settings.update(review_only=False, auto_approve_threshold=.50, ai_model='after')
        self.jobs = Jobs(self.library, 'base', settings_getter=lambda: dict(settings))
        self.addCleanup(self.jobs.pool.shutdown, wait=True, cancel_futures=True)
        self.addCleanup(self.jobs.shutdown)
        with patch('castwell.processing.detect_ads', side_effect=self.policy_candidate) as detect, \
             patch('castwell.processing.render_audio') as render:
            self.jobs.submit(identifier, 'process')
            finished = self.wait()
        self.assertEqual(finished['status'], 'review', finished['error'])
        self.assertFalse(finished['cuts'][0]['approved'])
        self.assertEqual({key: detect.call_args.kwargs['config'][key] for key in expected}, expected)
        self.assertEqual(detect.call_args.kwargs['config']['ai_model'], 'before')
        render.assert_not_called()

    def test_legacy_pending_job_without_policy_keeps_current_settings_behavior(self):
        identifier = self.episode(transcript=TRANSCRIPT, status='error', pending_job={
            'operation': 'process', 'options': {'remove_ads': True, 'detector': None,
                'redetect': False, 'retranscribe': False, 'download_only': False},
            'prior_status': 'available', 'prior_progress': ''})
        self.jobs.settings_getter = lambda: {'review_only': False, 'auto_approve_threshold': .90}
        with patch('castwell.processing.detect_ads', side_effect=self.policy_candidate), \
             patch('castwell.processing.render_audio', return_value={'duration': 9, 'removed_seconds': 3}) as render:
            self.jobs.submit(identifier, 'process')
            finished = self.wait()
        self.assertEqual(finished['status'], 'ready', finished['error'])
        self.assertTrue(finished['cuts'][0]['approved'])
        render.assert_called_once()

    def test_new_policy_never_changes_existing_reviewed_cut_decisions(self):
        reviewed = [dict(CUT, approved=False, requires_review=True)]
        identifier = self.episode(transcript=TRANSCRIPT, cuts=reviewed, analysis_done=True)
        self.jobs.settings_getter = lambda: {'review_only': False, 'auto_approve_threshold': .50}
        with patch('castwell.processing.detect_ads') as detect, patch('castwell.processing.render_audio') as render:
            self.jobs.submit(identifier, 'process')
            finished = self.wait()
        self.assertEqual(finished['cuts'], reviewed)
        detect.assert_not_called()
        render.assert_not_called()

    def test_explicit_new_redetection_replaces_a_failed_jobs_policy(self):
        identifier = self.episode(transcript=TRANSCRIPT)
        settings = {'review_only': True, 'auto_approve_threshold': .97}
        self.jobs.settings_getter = lambda: dict(settings)
        with patch('castwell.processing.detect_ads', side_effect=processing.ProcessingError('Unavailable')):
            self.jobs.submit(identifier, 'process')
            self.assertEqual(self.wait()['status'], 'error')
        settings.update(review_only=False, auto_approve_threshold=.90)
        with patch('castwell.processing.detect_ads', side_effect=self.policy_candidate), \
             patch('castwell.processing.render_audio', return_value={'duration': 9, 'removed_seconds': 3}) as render:
            self.jobs.submit(identifier, 'process', {'redetect': True})
            finished = self.wait()
        self.assertEqual(finished['status'], 'ready', finished['error'])
        self.assertTrue(finished['cuts'][0]['approved'])
        render.assert_called_once()

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

    def test_failed_redetection_retries_same_intent_after_reopening_library(self):
        identifier = self.episode(transcript=TRANSCRIPT, cuts=[CUT], analysis_done=True)
        with patch('castwell.processing.detect_ads', side_effect=processing.ProcessingError('Classifier unavailable')):
            self.jobs.submit(identifier, 'process', {'redetect': True, 'detector': 'ai'})
            failed = self.wait()
        self.assertEqual(failed['status'], 'error')
        self.assertEqual(failed['cuts'], [CUT])
        self.assertTrue(failed['analysis_done'])
        self.jobs.shutdown()
        self.jobs.pool.shutdown(wait=True)
        self.library = Library(self.library.root)
        self.library.recover()
        self.jobs = Jobs(self.library, 'base')
        self.addCleanup(self.jobs.pool.shutdown, wait=True, cancel_futures=True)
        self.addCleanup(self.jobs.shutdown)
        with patch('castwell.processing.detect_ads', return_value=[]) as detect, patch('castwell.processing.transcribe') as transcribe:
            self.jobs.submit(identifier, 'process', {'remove_ads': True})
            finished = self.wait()
        self.assertEqual(finished['status'], 'ready', finished['error'])
        self.assertEqual(finished['cuts'], [])
        self.assertIsNone(finished['pending_job'])
        self.assertEqual(detect.call_args.kwargs['detector'], 'ai')
        transcribe.assert_not_called()

    def test_bad_download_is_removed_and_retry_downloads_again(self):
        identifier = self.episode(audio=False)
        def download(url, destination, **kwargs):
            destination.write_bytes(b'HTTP 200 error page')
            return destination
        self.probe.side_effect = processing.InvalidAudioError('Invalid audio')
        with patch('castwell.jobs.download_media', side_effect=download) as fetch:
            self.jobs.submit(identifier, 'process', {'download_only': True})
            failed = self.wait()
            self.assertEqual(failed['status'], 'error')
            self.assertFalse(Path(failed['audio']).exists())
            self.probe.side_effect = None
            with patch('castwell.processing.transcribe') as transcribe:
                self.jobs.submit(identifier, 'process')
                finished = self.wait()
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(finished['status'], 'downloaded', finished['error'])
        self.assertTrue(Path(finished['audio']).is_file())
        transcribe.assert_not_called()

    def test_probe_installation_error_does_not_remove_cached_audio(self):
        identifier = self.episode()
        original = Path(self.library.get(identifier)['audio'])
        self.probe.side_effect = processing.ProcessingError('ffprobe is required')
        self.jobs.submit(identifier, 'process', {'download_only': True})
        self.assertEqual(self.wait()['status'], 'error')
        self.assertTrue(original.is_file())

    def test_invalid_local_upload_is_never_deleted_by_retry(self):
        identifier = self.episode()
        self.library.import_entries([{'id': identifier, 'media_url': '', 'source': 'local'}])
        original = Path(self.library.get(identifier)['audio'])
        self.probe.side_effect = processing.InvalidAudioError('Invalid audio')
        self.jobs.submit(identifier, 'process', {'download_only': True})
        self.assertEqual(self.wait()['status'], 'error')
        self.assertTrue(original.is_file())

    def test_audio_only_preserves_prepared_status_and_reviewed_cuts(self):
        for status, cuts in (('ready', []), ('review', [dict(CUT, approved=False)])):
            identifier = self.episode(status, transcript=TRANSCRIPT, status=status, cuts=cuts,
                                      analysis_done=True, progress='Previously prepared')
            with patch('castwell.processing.detect_ads') as detect, patch('castwell.processing.transcribe') as transcribe:
                self.jobs.submit(identifier, 'process', {'download_only': True})
                finished = self.wait(identifier)
            self.assertEqual(finished['status'], status)
            self.assertEqual(finished['progress'], 'Previously prepared')
            self.assertEqual(finished['cuts'], cuts)
            detect.assert_not_called()
            transcribe.assert_not_called()

    def test_failed_retranscription_keeps_previous_text_and_retries_new_model(self):
        identifier = self.episode(transcript=TRANSCRIPT, cuts=[CUT], analysis_done=True)
        self.jobs.settings_getter = lambda: {'transcription_model': 'small', 'language': 'fr'}
        with patch('castwell.processing.transcribe', side_effect=processing.ProcessingError('Model unavailable')):
            self.jobs.submit(identifier, 'process', {'retranscribe': True})
            failed = self.wait()
        self.assertEqual(failed['transcript'], TRANSCRIPT)
        self.assertEqual(failed['cuts'], [CUT])
        fresh = dict(TRANSCRIPT, model='small', language='fr', segments=[dict(TRANSCRIPT['segments'][0], text='Updated speech text')])
        with patch('castwell.processing.transcribe', return_value=fresh) as transcribe, \
             patch('castwell.processing.detect_ads') as detect, \
             patch('castwell.processing.render_audio', return_value={'duration': 9, 'removed_seconds': 3}):
            self.jobs.submit(identifier, 'process')
            finished = self.wait()
        self.assertEqual(finished['status'], 'ready', finished['error'])
        self.assertEqual(finished['transcript'], fresh)
        self.assertEqual(finished['cuts'], [CUT])
        self.assertEqual(transcribe.call_args.kwargs['model'], 'small')
        self.assertEqual(transcribe.call_args.kwargs['language'], 'fr')
        detect.assert_not_called()
        backup = next(self.library.directory(identifier).glob('previous-transcript-*.json'))
        self.assertEqual(json.loads(backup.read_text()), TRANSCRIPT)

    def test_cancelled_retranscription_preserves_existing_text_and_intent(self):
        identifier = self.episode(transcript=TRANSCRIPT, cuts=[CUT], analysis_done=True)
        def transcribe(*args, **kwargs):
            self.jobs.cancel(identifier)
            raise processing.ProcessingCancelled('Cancelled during inference')
        with patch('castwell.processing.transcribe', side_effect=transcribe):
            self.jobs.submit(identifier, 'process', {'retranscribe': True})
            cancelled = self.wait()
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertEqual(cancelled['transcript'], TRANSCRIPT)
        self.assertEqual(cancelled['cuts'], [CUT])
        self.assertTrue(cancelled['pending_job']['options']['retranscribe'])

    def test_completed_retranscription_is_checkpointed_before_cancelled_redetection(self):
        identifier = self.episode(transcript=TRANSCRIPT, analysis_done=True)
        fresh = dict(TRANSCRIPT, segments=[dict(TRANSCRIPT['segments'][0], text='Completed replacement')])
        def transcribe(*args, **kwargs):
            self.jobs.cancel(identifier)
            return fresh
        with patch('castwell.processing.transcribe', side_effect=transcribe), patch('castwell.processing.detect_ads') as detect:
            self.jobs.submit(identifier, 'process', {'retranscribe': True, 'redetect': True})
            cancelled = self.wait()
        detect.assert_not_called()
        self.assertEqual(cancelled['transcript'], fresh)
        self.assertFalse(cancelled['pending_job']['options']['retranscribe'])
        with patch('castwell.processing.transcribe') as transcribe, patch('castwell.processing.detect_ads', return_value=[]) as detect:
            self.jobs.submit(identifier, 'process')
            finished = self.wait()
        self.assertEqual(finished['status'], 'ready')
        transcribe.assert_not_called()
        detect.assert_called_once()

    def test_redetection_is_not_repeated_after_only_rendering_failed(self):
        identifier = self.episode(transcript=TRANSCRIPT, analysis_done=True)
        with patch('castwell.processing.detect_ads', return_value=[CUT]) as detect, \
             patch('castwell.processing.render_audio', side_effect=processing.ProcessingError('Temporary render failure')):
            self.jobs.submit(identifier, 'process', {'redetect': True})
            self.assertEqual(self.wait()['status'], 'error')
        detect.assert_called_once()
        with patch('castwell.processing.detect_ads') as detect, \
             patch('castwell.processing.render_audio', return_value={'duration': 9, 'removed_seconds': 3}):
            self.jobs.submit(identifier, 'process')
            self.assertEqual(self.wait()['status'], 'ready')
        detect.assert_not_called()

    def test_retry_of_failed_manual_export_does_not_start_transcription(self):
        identifier = self.episode(cuts=[CUT], analysis_done=True)
        with patch('castwell.processing.render_audio', side_effect=processing.ProcessingError('Temporary export failure')):
            self.jobs.submit(identifier, 'render')
            self.assertEqual(self.wait()['status'], 'error')
        with patch('castwell.processing.transcribe') as transcribe, \
             patch('castwell.processing.render_audio', return_value={'duration': 9, 'removed_seconds': 3}) as render:
            self.jobs.submit(identifier, 'process')
            finished = self.wait()
        self.assertEqual(finished['status'], 'ready')
        render.assert_called_once()
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
