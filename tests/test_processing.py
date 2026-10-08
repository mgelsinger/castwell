"""Behavior tests for ad decisions, trust boundaries, and actual audio edits."""

import copy
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from castwell.processing import (
    ProcessingCancelled, ProcessingError, cleaned_transcript, detect_ads, probe_duration, render_audio,
    transcribe, transcript_text, validate_cuts, validate_transcript, waveform,
)


def transcript(*texts):
    return {"language": "en", "duration": len(texts) * 10.0,
            "segments": [{"id": index, "start": index * 10.0, "end": (index + 1) * 10.0, "text": text}
                         for index, text in enumerate(texts)]}


class FakeResponse:
    def __init__(self, answer):
        self.answer = answer

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": json.dumps(self.answer)}}]}


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"CASTWELL_AI_BASE_URL": "", "CASTWELL_AI_MODEL": "", "CASTWELL_AI_KEY": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_explicit_sponsor_with_closing_boundary_is_approved(self):
        source = transcript("We explore the rings of Saturn.", "This episode is sponsored by Acme.",
                            "Their product helps you plan meals.", "Use promo code SPACE for a free trial.",
                            "Now back to our conversation.", "Saturn has many moons.")
        cuts = detect_ads(source)
        self.assertEqual([(item["start"], item["end"], item["approved"]) for item in cuts], [(10, 40, True)])

    def test_host_read_without_bumper_stays_reviewable_in_baseline(self):
        source = transcript("I started using Acme for my groceries.",
                            "Use promo code SPACE for a free trial.", "The ancient oceans were different.")
        cuts = detect_ads(source, "heuristic")
        self.assertEqual(len(cuts), 1)
        self.assertFalse(cuts[0]["approved"])

    def test_recovered_speech_blocks_legacy_heuristic_automatic_approval(self):
        source = transcript("We explore Saturn.", "This episode is sponsored by Acme.",
                            "Use promo code SPACE for a free trial.", "Now back to our conversation.")
        source["segments"][1]["asr_recovered"] = True
        cuts = detect_ads(source, "heuristic", config={"ai_policy": "legacy", "review_only": False})
        self.assertEqual([(cut["start"], cut["end"]) for cut in cuts], [(10, 30)])
        self.assertFalse(cuts[0]["approved"])
        self.assertTrue(cuts[0]["requires_review"])
        self.assertIn("provisional recovered speech", cuts[0]["reason"])

    def test_touching_recovered_editorial_does_not_change_legacy_ad_approval(self):
        source = transcript("We explore Saturn.", "This episode is sponsored by Acme.",
                            "Use promo code SPACE for a free trial.", "Now back to our conversation.")
        source["segments"][3]["asr_recovered"] = True
        cuts = detect_ads(source, "heuristic", config={"ai_policy": "legacy", "review_only": False})
        self.assertEqual([(cut["start"], cut["end"], cut["approved"]) for cut in cuts], [(10, 30, True)])

    def test_editorial_discussion_of_advertising_is_not_auto_removed(self):
        source = transcript("We discuss an example: this episode is sponsored by Acme.",
                            "Advertisers ask listeners to use promo code SALE.", "Now back to our conversation.")
        self.assertFalse(any(item["approved"] for item in detect_ads(source)))
        self.assertEqual(detect_ads(transcript("We analyzed the advertising industry and its effect on culture.")), [])

    def test_bad_configuration_cannot_report_successful_empty_detection(self):
        with patch.dict(os.environ, {"CASTWELL_AI_MODEL": "some-local-model"}):
            with self.assertRaisesRegex(ProcessingError, "requires both"):
                detect_ads(transcript("No advertisement."))
        with self.assertRaises(ValueError):
            detect_ads(transcript("No advertisement."), "unknown")

    def ai_environment(self):
        return patch.dict(os.environ, {"CASTWELL_AI_BASE_URL": "http://localhost:11434/v1", "CASTWELL_AI_MODEL": "local-model"})

    def test_ai_can_mark_complete_host_read_without_bumper(self):
        source = transcript("I started using Acme for groceries.", "The deliveries save me hours.",
                            "Try it with my promo code SPACE.", "The ancient oceans were different.")
        answer = {"ads": [{"segment_ids": [0, 1, 2], "confidence": 0.96, "reason": "Host sells grocery deliveries and offers a discount"}]}
        with self.ai_environment(), patch("requests.post", return_value=FakeResponse(answer)) as request:
            cuts = detect_ads(source)
        self.assertEqual([(cut["start"], cut["end"], cut["approved"]) for cut in cuts], [(0, 30, True)])
        self.assertEqual(request.call_args.args[0], "http://localhost:11434/v1/chat/completions")
        sent = request.call_args.kwargs["json"]["messages"][1]["content"]
        self.assertIn("ancient oceans", sent)
        self.assertNotIn("Authorization", request.call_args.kwargs["headers"])

    def test_legacy_ai_only_demotes_cuts_overlapping_recovered_speech(self):
        source = transcript("A recovered promotion.", "Editorial discussion.", "Another promotion.")
        source["segments"][0]["asr_recovered"] = True
        answer = {"ads": [{"segment_ids": [0], "confidence": .99, "reason": "Promotion one"},
                          {"segment_ids": [2], "confidence": .99, "reason": "Promotion two"}]}
        with self.ai_environment(), patch("requests.post", return_value=FakeResponse(answer)):
            cuts = detect_ads(source, "ai", config={"ai_policy": "legacy", "review_only": False})
        self.assertEqual([(cut["start"], cut["end"], cut["approved"]) for cut in cuts], [(0, 10, False), (20, 30, True)])
        self.assertTrue(cuts[0]["requires_review"])
        self.assertIn("recovered speech", cuts[0]["reason"])
        self.assertEqual(cuts[1]["reason"], "Promotion two")

    def test_ai_must_use_real_contiguous_segment_ids_and_numeric_confidence(self):
        cases = [
            {"segment_ids": [90], "confidence": 0.99, "reason": "ad"},
            {"segment_ids": [0, 2], "confidence": 0.99, "reason": "ad"},
            {"segment_ids": [0, 0], "confidence": 0.99, "reason": "ad"},
            {"segment_ids": [True], "confidence": 0.99, "reason": "ad"},
            {"segment_ids": [0], "confidence": float("nan"), "reason": "ad"},
            {"segment_ids": [0], "confidence": "0.99", "reason": "ad"},
            {"start": 0, "end": 20, "confidence": 0.99, "reason": "ad"},
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate), self.ai_environment(), patch("requests.post", return_value=FakeResponse({"ads": [candidate]})):
                with self.assertRaisesRegex(ProcessingError, "invalid results"):
                    detect_ads(transcript("first", "second", "third"))

    def test_ai_transport_failure_is_visible(self):
        import requests
        with self.ai_environment(), patch("requests.post", side_effect=requests.Timeout("secret URL should not be shown")):
            with self.assertRaisesRegex(ProcessingError, "request failed") as error:
                detect_ads(transcript("Some text"))
        self.assertNotIn("secret URL", str(error.exception))

    def test_ai_conflicting_labels_cannot_override_uncertain_decisions(self):
        answer = {"ads": [{"segment_ids": [0], "confidence": 0.65, "reason": "Uncertain"},
                          {"segment_ids": [0], "confidence": 0.99, "reason": "Conflicting label"}]}
        with self.ai_environment(), patch("requests.post", return_value=FakeResponse(answer)):
            with self.assertRaisesRegex(ProcessingError, "invalid results"):
                detect_ads(transcript("Possibly promotional content"))

    def test_chunk_boundary_ads_use_context_without_cutting_editorial(self):
        source = transcript(*[f"Editorial sentence {index}" for index in range(100)])
        first = {"ads": [{"segment_ids": [78, 79, 80, 81], "confidence": 0.97, "reason": "A sponsor read"}]}
        second = {"ads": [{"segment_ids": [78, 79, 80, 81], "confidence": 0.96, "reason": "A sponsor read"}]}
        with self.ai_environment(), patch("requests.post", side_effect=[FakeResponse(first), FakeResponse(second)]) as request:
            cuts = detect_ads(source)
        self.assertEqual(request.call_count, 2)
        self.assertEqual([(cut["start"], cut["end"]) for cut in cuts], [(780, 820)])
        self.assertEqual(cuts[0]["confidence"], 0.96)

    def test_ai_low_confidence_does_not_join_an_approved_cut(self):
        answer = {"ads": [{"segment_ids": [0], "confidence": 0.99, "reason": "Clear ad"},
                          {"segment_ids": [1], "confidence": 0.75, "reason": "Mixed editorial and ad"}]}
        with self.ai_environment(), patch("requests.post", return_value=FakeResponse(answer)):
            cuts = detect_ads(transcript("Ad", "Mixed", "Editorial"))
        self.assertEqual([cut["approved"] for cut in cuts], [True, False])

    def test_transcript_validation_rejects_overlap_nan_and_duplicate_ids(self):
        source = transcript("first", "second")
        for field, value in [("start", 9), ("end", float("nan")), ("id", 0), ("end", 30), ("text", "")]:
            candidate = copy.deepcopy(source)
            candidate["segments"][1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_transcript(candidate)
        self.assertEqual(validate_transcript(source), source)

    def test_imported_transcript_can_omit_ids_and_duration(self):
        source = {"language": "en", "segments": [{"start": 1, "end": 5, "text": "Hello"}]}
        normalized = validate_transcript(source)
        self.assertEqual(normalized["duration"], 5)
        self.assertEqual(normalized["segments"][0]["id"], 0)
        self.assertEqual(validate_transcript(source, duration=8)["duration"], 8)
        with self.assertRaises(ValueError):
            validate_transcript(source, duration=4)

    def test_cut_validation_requires_valid_time_and_boolean_approval(self):
        for candidate in [{"start": -1, "end": 2}, {"start": 2, "end": 1}, {"start": 0, "end": 21},
                          {"start": 0, "end": 5, "approved": "false"}, {"start": 0, "end": 5, "confidence": 2}]:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                validate_cuts([candidate], 20)
        self.assertFalse(validate_cuts([{"start": 0, "end": 2}], 20)[0]["approved"])

    def test_remapping_and_caption_exports(self):
        source = transcript("Introduction", "Commercial", "Science")
        cleaned = cleaned_transcript(source, [{"start": 10, "end": 20, "approved": True}])
        self.assertEqual(cleaned["duration"], 20)
        self.assertEqual([(s["text"], s["start"], s["end"]) for s in cleaned["segments"]],
                         [("Introduction", 0, 10), ("Science", 10, 20)])
        self.assertNotIn("Commercial", transcript_text(cleaned))
        self.assertIn("00:00:10,000 --> 00:00:20,000", transcript_text(cleaned, "srt"))
        self.assertTrue(transcript_text(cleaned, "vtt").startswith("WEBVTT\n"))
        partial = cleaned_transcript(source, [{"start": 12, "end": 18, "approved": True}])
        self.assertTrue(partial["segments"][1]["partial"])
        self.assertIn("Partial segment", transcript_text(partial, "vtt"))

    def test_word_alignment_removes_cut_text_and_maps_both_timelines(self):
        source = transcript("Hello dear sponsor listeners welcome.")
        source["segments"][0]["words"] = [
            {"word": "Hello", "start": 0, "end": 1, "probability": .98},
            {"word": " dear", "start": 1, "end": 2},
            {"word": " sponsor", "start": 2, "end": 4},
            {"word": " listeners", "start": 4, "end": 6},
            {"word": " welcome.", "start": 6, "end": 10},
        ]
        original = copy.deepcopy(source)
        cleaned = cleaned_transcript(source, [{"start": 2, "end": 6, "approved": True}])
        segment = cleaned["segments"][0]
        self.assertEqual(segment["text"], "Hello dear welcome.")
        self.assertNotIn("partial", segment)
        self.assertEqual(segment["words"][-1], {"word": " welcome.", "start": 2, "end": 6,
                                               "original_start": 6, "original_end": 10})
        self.assertEqual(cleaned["original_duration"], 10)
        self.assertEqual(cleaned["timeline"], [
            {"original_start": 0, "original_end": 2, "start": 0, "end": 2},
            {"original_start": 6, "original_end": 10, "start": 2, "end": 6},
        ])
        self.assertNotIn("sponsor", transcript_text(cleaned, "vtt"))
        self.assertEqual(source, original)

    def test_word_midpoint_policy_clips_split_words_and_preserves_spacing(self):
        source = transcript("Hello world!")
        source["segments"][0]["words"] = [{"word": "Hello", "start": 0, "end": 1},
                                          {"word": "world", "start": 1, "end": 2},
                                          {"word": "!", "start": 2, "end": 2}]
        cleaned = cleaned_transcript(source, [{"start": .75, "end": 1.25, "approved": True}])
        words = cleaned["segments"][0]["words"]
        self.assertEqual(cleaned["segments"][0]["text"], "Hello world!")
        self.assertEqual([(w["start"], w["end"]) for w in words], [(0, .75), (.75, 1.5), (1.5, 1.5)])

    def test_word_validation_rejects_overlap_bad_probability_and_outside_segment(self):
        source = transcript("Hello world")
        source["segments"][0]["words"] = [{"word": "Hello", "start": 0, "end": 1},
                                          {"word": " world", "start": 1, "end": 2}]
        for field, value in [("start", .5), ("end", 11), ("start", float("nan")),
                             ("probability", 1.5), ("probability", True), ("word", "")]:
            candidate = copy.deepcopy(source)
            candidate["segments"][0]["words"][1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_transcript(candidate)
        self.assertEqual(validate_transcript(source), source)

    def test_empty_word_alignment_keeps_partial_warning(self):
        source = transcript("Untimed words")
        source["segments"][0]["words"] = []
        result = cleaned_transcript(source, [{"start": 1, "end": 2, "approved": True}])
        self.assertTrue(result["segments"][0]["partial"])

    def test_threshold_and_review_only_apply_to_both_detectors(self):
        source = transcript("This episode is sponsored by Acme.", "Use promo code SPACE for a free trial.",
                            "Now back to our conversation.")
        self.assertFalse(detect_ads(source, config={"auto_approve_threshold": .99})[0]["approved"])
        self.assertFalse(detect_ads(source, config={"review_only": True})[0]["approved"])
        answer = {"ads": [{"segment_ids": [0], "confidence": .96, "reason": "Ad"},
                          {"segment_ids": [1], "confidence": .99, "reason": "Ad"}]}
        config = {"ai_base_url": "https://example.test/v1", "ai_model": "classifier", "auto_approve_threshold": .98}
        with patch("requests.post", return_value=FakeResponse(answer)) as request:
            cuts = detect_ads(source, config=config)
            self.assertEqual([cut["approved"] for cut in cuts], [False, True])
            self.assertEqual(request.call_args.args[0], "https://example.test/v1/chat/completions")
            self.assertEqual(cuts[0]["sources"], ["ai", "heuristic"])
            self.assertFalse(any(c["approved"] for c in detect_ads(source, config=dict(config, review_only=True))))
        for config in [{"auto_approve_threshold": True}, {"auto_approve_threshold": 2}, {"review_only": "false"}]:
            with self.subTest(config=config), self.assertRaises(ValueError):
                detect_ads(source, config=config)

    def test_ai_disagreement_keeps_explicit_cue_for_review(self):
        source = transcript("This episode is sponsored by Acme.", "Use promo code SPACE for a free trial.",
                            "Now back to our conversation.")
        with self.ai_environment(), patch("requests.post", return_value=FakeResponse({"ads": []})):
            cuts = detect_ads(source, config={"auto_approve_threshold": .5})
        self.assertEqual(len(cuts), 1)
        self.assertFalse(cuts[0]["approved"])
        self.assertTrue(cuts[0]["requires_review"])

    def test_word_alignment_separates_ads_from_editorial_in_same_whisper_segment(self):
        text = "Trees grow. This episode is sponsored by Acme. Use code TREE for a free trial. Now back to the forest."
        tokens = text.split()
        source = {"language": "en", "duration": len(tokens), "segments": [{
            "id": 0, "start": 0, "end": len(tokens), "text": text,
            "words": [{"word": " " + token, "start": index, "end": index + 1}
                      for index, token in enumerate(tokens)],
        }]}
        answer = {"ads": [{"segment_ids": [1, 2], "confidence": .98, "reason": "Complete sponsor read"}]}
        with self.ai_environment(), patch("requests.post", return_value=FakeResponse(answer)) as request:
            cuts = detect_ads(source)
        payload = json.loads(request.call_args.kwargs["json"]["messages"][1]["content"])
        self.assertEqual([segment["text"] for segment in payload["segments"]], [
            "Trees grow.", "This episode is sponsored by Acme.",
            "Use code TREE for a free trial.", "Now back to the forest.",
        ])
        self.assertEqual([(cut["start"], cut["end"]) for cut in cuts], [(2, tokens.index("Now"))])
        self.assertEqual(cleaned_transcript(source, cuts)["segments"][0]["text"],
                         "Trees grow. Now back to the forest.")
        self.assertEqual([(c["start"], c["end"]) for c in detect_ads(source, "heuristic")],
                         [(2, tokens.index("Now"))])

    def test_unmatched_portion_of_commercial_cue_is_still_reviewable(self):
        source = transcript("This episode is sponsored by Acme.", "Use promo code SPACE for a free trial.",
                            "Now back to our conversation.")
        answer = {"ads": [{"segment_ids": [0], "confidence": .97, "reason": "Sponsor"}]}
        with self.ai_environment(), patch("requests.post", return_value=FakeResponse(answer)):
            cuts = detect_ads(source)
        self.assertEqual([(cut["start"], cut["end"], cut["approved"]) for cut in cuts],
                         [(0, 10, True), (10, 20, False)])

    def test_quoted_sales_pitch_in_research_cannot_be_auto_removed(self):
        source = transcript("We are researching how advertising affects children.",
                            "One advertisement says quote buy now and get a free toy end quote.",
                            "Our experiment measures the effect of this sales tactic.")
        answer = {"ads": [{"segment_ids": [1], "confidence": .99, "reason": "A commercial offer"}]}
        with self.ai_environment(), patch("requests.post", return_value=FakeResponse(answer)):
            cuts = detect_ads(source)
        self.assertTrue(cuts[0]["requires_review"])
        self.assertFalse(cuts[0]["approved"])
        self.assertIn("editorial analysis", cuts[0]["reason"])

    def test_configurable_classifier_window_bounds_context_and_timeout(self):
        source = transcript(*["Editorial sentence. " * 5 for _ in range(10)])
        with self.ai_environment(), patch.dict(os.environ, {"CASTWELL_AI_WINDOW_CHARS": "256",
                "CASTWELL_AI_CONTEXT_SEGMENTS": "1", "CASTWELL_AI_TIMEOUT": "300"}), \
                patch("requests.post", return_value=FakeResponse({"ads": []})) as request:
            self.assertEqual(detect_ads(source), [])
        self.assertEqual(request.call_count, 5)
        for call in request.call_args_list:
            payload = json.loads(call.kwargs["json"]["messages"][1]["content"])
            self.assertLessEqual(len(payload["segments"]), 4)
            self.assertEqual(call.kwargs["timeout"], (10, 300))

    def test_classifier_refuses_oversized_single_segment_and_invalid_limits(self):
        with self.ai_environment(), patch.dict(os.environ, {"CASTWELL_AI_WINDOW_CHARS": "256"}), \
                patch("requests.post") as request:
            with self.assertRaisesRegex(ProcessingError, "text window"):
                detect_ads(transcript("X" * 513))
            request.assert_not_called()
        with self.ai_environment(), patch.dict(os.environ, {"CASTWELL_AI_TIMEOUT": "nan"}):
            with self.assertRaisesRegex(ProcessingError, "valid bounded numbers"):
                detect_ads(transcript("Editorial"))

    def test_classifier_progress_and_cancellation_stop_following_windows(self):
        cancelled = False
        progress = []
        def respond(*args, **kwargs):
            nonlocal cancelled
            cancelled = True
            return FakeResponse({"ads": []})
        with self.ai_environment(), patch("requests.post", side_effect=respond) as request:
            with self.assertRaises(ProcessingCancelled):
                detect_ads(transcript(*["Content" for _ in range(100)]), progress=progress.append,
                           should_cancel=lambda: cancelled)
        self.assertEqual(request.call_count, 1)
        self.assertIn("1–80 of 100", progress[0])

    def test_classifier_retries_transient_status_but_not_bad_requests(self):
        import requests
        transient = Mock(status_code=429, headers={"Retry-After": "0"})
        transient.raise_for_status.side_effect = requests.HTTPError(response=transient)
        with self.ai_environment(), patch("requests.post", side_effect=[transient, FakeResponse({"ads": []})]) as request:
            self.assertEqual(detect_ads(transcript("Editorial")), [])
            self.assertEqual(request.call_count, 2)
        bad = Mock(status_code=400)
        bad.raise_for_status.side_effect = requests.HTTPError(response=bad)
        with self.ai_environment(), patch("requests.post", return_value=bad) as request:
            with self.assertRaisesRegex(ProcessingError, "HTTP 400"):
                detect_ads(transcript("Editorial"))
            self.assertEqual(request.call_count, 1)

    def test_transcribe_requests_word_alignment_language_and_cache(self):
        word = SimpleNamespace(word=" Hello", start=0, end=2, probability=.95)
        segment = SimpleNamespace(start=0, end=2, text=" Hello", words=[word])
        engine = Mock()
        engine.transcribe.return_value = (iter([segment]), SimpleNamespace(language="en"))
        with patch.dict("sys.modules", {"faster_whisper": Mock()}), \
                patch("castwell.processing.probe_duration", return_value=3), \
                patch("castwell.processing._cached_model", return_value=engine) as cached:
            result = transcribe(Path("audio.wav"), model="tiny", language="EN", model_cache="/tmp/models")
        cached.assert_called_once_with("tiny", "/tmp/models")
        self.assertTrue(engine.transcribe.call_args.kwargs["word_timestamps"])
        self.assertEqual(engine.transcribe.call_args.kwargs["language"], "en")
        self.assertEqual(result["segments"][0]["words"], [{"word": " Hello", "start": 0, "end": 2, "probability": .95}])

    def test_transcription_cancel_closes_generator_before_next_inference(self):
        cancelled, next_called, closed = False, False, False
        def segments():
            nonlocal next_called, closed
            try:
                yield SimpleNamespace(start=0, end=1, text="Hello", words=[])
                next_called = True
                yield SimpleNamespace(start=1, end=2, text="World", words=[])
            finally:
                closed = True
        def progress(message):
            nonlocal cancelled
            if message.startswith("Transcribing:"):
                cancelled = True
        engine = Mock()
        engine.transcribe.return_value = (segments(), SimpleNamespace(language="en"))
        with patch.dict("sys.modules", {"faster_whisper": Mock()}), \
                patch("castwell.processing.probe_duration", return_value=3), \
                patch("castwell.processing._cached_model", return_value=engine):
            with self.assertRaises(ProcessingCancelled):
                transcribe(Path("audio.wav"), progress=progress, should_cancel=lambda: cancelled)
        self.assertFalse(next_called)
        self.assertTrue(closed)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is needed for real rendering")
class AudioRenderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.audio = Path(self.temporary.name) / "original.wav"
        # Four one-second plateaus permit verifying exact content, not duration
        # alone. A decoder/concat error cannot pass by merely producing silence.
        with wave.open(str(self.audio), "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(8000)
            file.writeframes(b"".join(struct.pack("<h", value) * 8000 for value in (1000, 2000, 3000, 4000)))

    def test_actual_audio_cuts_union_overlaps_and_ignore_unapproved(self):
        original = self.audio.read_bytes()
        output = self.audio.parent / "cleaned.wav"
        result = render_audio(self.audio, output, [
            {"start": 1, "end": 2.5, "approved": True},
            {"start": 2, "end": 3, "approved": True},
            {"start": 0, "end": 1, "approved": False},
        ])
        self.assertAlmostEqual(result["duration"], 2, places=3)
        self.assertAlmostEqual(result["removed_seconds"], 2, places=3)
        self.assertEqual(self.audio.read_bytes(), original)
        with wave.open(str(output), "rb") as file:
            samples = struct.unpack("<" + "h" * file.getnframes(), file.readframes(file.getnframes()))
        self.assertEqual(samples, (1000,) * 8000 + (4000,) * 8000)

    def test_render_refuses_original_and_complete_deletion(self):
        with self.assertRaisesRegex(ValueError, "differ"):
            render_audio(self.audio, self.audio, [])
        with self.assertRaisesRegex(ValueError, "entire recording"):
            render_audio(self.audio, self.audio.parent / "cleaned.wav", [{"start": 0, "end": 4, "approved": True}])

    def test_failed_render_preserves_previous_output(self):
        output = self.audio.parent / "cleaned.wav"
        output.write_bytes(b"existing output")
        popen = subprocess.Popen
        def launch(args, **kwargs):
            if Path(args[0]).stem.lower() == "ffmpeg":
                args = [sys.executable, "-c", "raise SystemExit(1)"]
            return popen(args, **kwargs)
        with patch("castwell.processing.subprocess.Popen", side_effect=launch):
            with self.assertRaises(ProcessingError):
                render_audio(self.audio, output, [])
        self.assertEqual(output.read_bytes(), b"existing output")

    def test_probe_rejects_non_audio(self):
        bad_audio = self.audio.parent / "invalid.mp3"
        bad_audio.write_text("not audio")
        with self.assertRaises(ProcessingError):
            probe_duration(bad_audio)

    def test_cleaned_audio_drops_chapters_from_original_timeline(self):
        metadata = self.audio.parent / "chapters.txt"
        metadata.write_text(";FFMETADATA1\ntitle=Example episode\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=4000\ntitle=Original chapter\n")
        original = self.audio.parent / "chaptered.m4a"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(self.audio),
                        "-f", "ffmetadata", "-i", str(metadata), "-map_metadata", "1", "-c:a", "aac", str(original)],
                       check=True, capture_output=True)
        output = self.audio.parent / "cleaned.m4a"
        render_audio(original, output, [{"start": 1, "end": 2, "approved": True}])
        def chapters(path):
            info = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(path)],
                                  check=True, capture_output=True, text=True)
            return json.loads(info.stdout)["chapters"]
        self.assertEqual(len(chapters(original)), 1)
        self.assertEqual(chapters(output), [])

    def test_waveform_reports_real_amplitudes_and_keeps_original(self):
        original = self.audio.read_bytes()
        result = waveform(self.audio, bins=4)
        self.assertEqual(result["duration"], 4)
        for peak, sample in zip(result["peaks"], (1000, 2000, 3000, 4000)):
            self.assertAlmostEqual(peak, sample / 32768, places=5)
        self.assertEqual(self.audio.read_bytes(), original)
        for bins in (0, 20001, True, 1.5):
            with self.subTest(bins=bins), self.assertRaises(ValueError):
                waveform(self.audio, bins=bins)

    def test_cancel_render_terminates_child_and_preserves_previous_output(self):
        output = self.audio.parent / "cleaned.wav"
        output.write_bytes(b"existing output")
        original = self.audio.read_bytes()
        calls = 0
        children = []
        popen = subprocess.Popen
        def launch(*args, **kwargs):
            process = popen(*args, **kwargs)
            if Path(args[0][0]).stem.lower() == "ffmpeg":
                children.append(process)
            return process
        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 3
        with patch("castwell.processing.subprocess.Popen", side_effect=launch):
            with self.assertRaises(ProcessingCancelled):
                render_audio(self.audio, output, [], should_cancel=cancelled)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(output.read_bytes(), b"existing output")
        self.assertEqual(self.audio.read_bytes(), original)
        self.assertEqual(list(self.audio.parent.glob(".castwell-render-*")), [])

    def test_cancel_waveform_terminates_child(self):
        calls = 0
        children = []
        popen = subprocess.Popen
        def launch(*args, **kwargs):
            process = popen(*args, **kwargs)
            if Path(args[0][0]).stem.lower() == "ffmpeg":
                children.append(process)
            return process
        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 2
        with patch("castwell.processing.subprocess.Popen", side_effect=launch):
            with self.assertRaises(ProcessingCancelled):
                waveform(self.audio, should_cancel=cancelled)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_cancel_waveform_while_decoder_has_no_output(self):
        children = []
        popen = subprocess.Popen
        def launch(args, **kwargs):
            if Path(args[0]).stem.lower() != "ffmpeg":
                return popen(args, **kwargs)
            process = popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            children.append(process)
            return process
        started = time.monotonic()
        with patch("castwell.processing.subprocess.Popen", side_effect=launch):
            with self.assertRaises(ProcessingCancelled):
                waveform(self.audio, should_cancel=lambda: bool(children))
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed)

    def test_waveform_short_silent_audio_and_invalid_audio(self):
        silence = self.audio.parent / "silence.wav"
        with wave.open(str(silence), "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(8000)
            file.writeframes(b"\0\0" * 8)
        result = waveform(silence)
        self.assertAlmostEqual(result["duration"], .001)
        self.assertEqual(result["peaks"], [0] * 700)
        bad = self.audio.parent / "invalid.wav"
        bad.write_text("invalid")
        with self.assertRaises(ProcessingError):
            waveform(bad)


if __name__ == "__main__":
    unittest.main()
