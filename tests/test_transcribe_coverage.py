"""Coverage runs after CUDA speech resources are released, without inference."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from castwell import audio_coverage, gpu_phases, processing


@pytest.fixture
def speech(monkeypatch):
    phases = []
    monkeypatch.setitem(sys.modules, 'faster_whisper', Mock())
    monkeypatch.setenv('CASTWELL_CLASSIFIER_SLEEP_ADDRESS', 'http://127.0.0.1:8089/v1')
    monkeypatch.setattr(processing, 'probe_duration', lambda audio: 20)
    monkeypatch.setattr(gpu_phases, 'wait_classifier_sleep', lambda *args, **options: phases.append('sleep'))
    def rows():
        try:
            yield SimpleNamespace(start=0, end=4, text='Opening.', words=[])
            yield SimpleNamespace(start=14, end=20, text='Return.', words=[])
        finally:
            phases.append('source_closed')
    engine = Mock()
    engine.transcribe.side_effect = lambda *args, **options: (rows(), SimpleNamespace(language='en'))
    cached = Mock(return_value=engine)
    cached.cache_clear.side_effect = lambda: phases.append('speech_released')
    monkeypatch.setattr(processing, '_cached_model', cached)
    return SimpleNamespace(phases=phases, cached=cached, engine=engine)


def test_coverage_receives_completed_transcript_after_lock_and_model_release(speech, monkeypatch):
    progress = []
    callback = progress.append
    def coverage(audio, transcript, **options):
        assert processing._MODEL_LOCK.acquire(blocking=False)
        processing._MODEL_LOCK.release()
        assert speech.cached.cache_clear.call_count == 1
        speech.phases.append('coverage')
        result = deepcopy(transcript)
        result['quality']['audio_coverage'] = {'cancelled': False, 'generic_gap_review_cleared': False}
        assert options['progress'] is callback
        return result
    check = Mock(side_effect=coverage)
    monkeypatch.setattr(audio_coverage, 'assess_audio_coverage', check)
    result = processing.transcribe(Path('local.wav'), recover_gaps=False, progress=callback)
    assert speech.phases == ['sleep', 'source_closed', 'speech_released', 'coverage']
    assert result['quality']['recovery']['enabled'] is False
    assert result['quality']['requires_review'] is True
    assert 'Checking audio without transcript text' in progress
    check.assert_called_once()


def test_coverage_cancellation_cannot_complete_the_episode(speech, monkeypatch):
    def cancelled(audio, transcript, **options):
        result = deepcopy(transcript)
        result['quality']['audio_coverage'] = {'cancelled': True}
        return result
    monkeypatch.setattr(audio_coverage, 'assess_audio_coverage', cancelled)
    with pytest.raises(processing.ProcessingCancelled, match='coverage'):
        processing.transcribe(Path('local.wav'), recover_gaps=False)
    assert speech.cached.cache_clear.call_count == 1
    assert processing._MODEL_LOCK.acquire(blocking=False)
    processing._MODEL_LOCK.release()


def test_cancellation_after_speech_release_prevents_coverage_model_use(speech, monkeypatch):
    state = {'cancelled': False}
    speech.cached.cache_clear.side_effect = lambda: state.update(cancelled=True)
    check = Mock()
    monkeypatch.setattr(audio_coverage, 'assess_audio_coverage', check)
    with pytest.raises(processing.ProcessingCancelled):
        processing.transcribe(Path('local.wav'), recover_gaps=False, should_cancel=lambda: state['cancelled'])
    check.assert_not_called()
    assert processing._MODEL_LOCK.acquire(blocking=False)
    processing._MODEL_LOCK.release()
