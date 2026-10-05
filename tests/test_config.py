"""Settings safety, persistent preferences, and offline model diagnostics."""

import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from castwell import config
from castwell.library import Library
from castwell.processing import ProcessingError


ENVIRONMENT = {
    "CASTWELL_TRANSCRIPTION_MODEL": "",
    "CASTWELL_MODEL_CACHE": "",
    "CASTWELL_AI_BASE_URL": "",
    "CASTWELL_AI_MODEL": "",
    "CASTWELL_AI_KEY": "",
}


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.library = Library(self.root)
        self.settings = config.Settings(self.library)
        self.environment = patch.dict(os.environ, ENVIRONMENT)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def install_model(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("model.bin", "config.json", "tokenizer.json"):
            (directory / name).write_bytes(b"test fixture")

    def test_defaults_are_usable_without_credentials(self):
        settings = self.settings.get()
        self.assertEqual(settings["transcription_model"], "base")
        self.assertEqual(settings["detector"], "auto")
        self.assertEqual(settings["auto_approve_threshold"], 0.90)
        self.assertIsNone(settings["language"])
        self.assertFalse(settings["review_only"])
        self.assertFalse(settings["ai_configured"])
        self.assertFalse(settings["key_configured"])
        self.assertEqual(settings["model_cache"], str(self.root / "models"))

    def test_update_persists_and_merges_preferences(self):
        self.settings.update({"language": "EN", "review_only": True, "auto_approve_threshold": 0.95})
        self.settings.update({"transcription_model": "small", "ai_base_url": "http://localhost:11434/v1/", "ai_model": "local-model"})
        reopened = config.Settings(Library(self.root)).get()
        self.assertEqual(reopened["language"], "en")
        self.assertTrue(reopened["review_only"])
        self.assertEqual(reopened["auto_approve_threshold"], 0.95)
        self.assertEqual(reopened["transcription_model"], "small")
        self.assertEqual(reopened["ai_base_url"], "http://localhost:11434/v1")
        self.assertTrue(reopened["ai_configured"])
        self.assertIsNone(self.settings.update({"language": "auto"})["language"])

    def test_invalid_updates_are_atomic(self):
        self.settings.update({"language": "fr"})
        invalid = [
            {"language": "English"}, {"transcription_model": ""},
            {"transcription_model": "model\x00name"}, {"detector": "made-up"},
            {"auto_approve_threshold": float("nan")}, {"auto_approve_threshold": True},
            {"auto_approve_threshold": 0.49}, {"auto_approve_threshold": 1.1},
            {"review_only": "false"}, {"ai_base_url": 42}, {"ai_model": None},
            {"model_cache": "/elsewhere"}, {"key_configured": True}, {"unknown": "value"},
        ]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.settings.update({"language": "de", **values})
            self.assertEqual(self.settings.get()["language"], "fr")

    def test_api_keys_are_never_accepted_or_returned(self):
        secret = "private-provider-secret"
        for key in ("ai_key", "api_key", "CASTWELL_AI_KEY"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.settings.update({key: secret})
        with patch.dict(os.environ, {"CASTWELL_AI_KEY": secret}):
            public = self.settings.get()
            self.assertTrue(public["key_configured"])
            self.assertNotIn(secret, json.dumps(public))
        self.assertNotIn(secret, json.dumps(self.library.get_settings()))

    def test_classifier_url_rejects_embedded_secrets_and_invalid_addresses(self):
        addresses = [
            "https://user:secret@provider.example/v1", "https://user@provider.example/v1",
            "https://provider.example/v1?key=secret", "https://provider.example/v1#secret",
            "https://provider.example/v1?", "https://provider.example/v1#",
            "file:///tmp/classifier", "https:///v1", "https://provider.example:broken/v1",
            "https://provider.example\\@other.example/v1", "https://provider.example/has space",
        ]
        for address in addresses:
            with self.subTest(address=address), self.assertRaises(ValueError) as error:
                self.settings.update({"ai_base_url": address})
            self.assertNotIn(address, str(error.exception))
        self.assertEqual(self.library.get_settings(), {})

    def test_environment_wins_and_locked_changes_do_not_persist(self):
        self.settings.update({"ai_model": "saved-model"})
        env = {"CASTWELL_AI_BASE_URL": "http://localhost:11434/v1/", "CASTWELL_AI_MODEL": "deployed-model",
               "CASTWELL_TRANSCRIPTION_MODEL": "tiny", "CASTWELL_MODEL_CACHE": str(self.root / "cache")}
        with patch.dict(os.environ, env):
            settings = self.settings.get()
            self.assertEqual(settings["ai_model"], "deployed-model")
            self.assertEqual(settings["transcription_model"], "tiny")
            self.assertEqual(set(settings["env_overrides"]), {"transcription_model", "model_cache", "ai_base_url", "ai_model"})
            with self.assertRaisesRegex(ValueError, "CASTWELL_AI_MODEL"):
                self.settings.update({"language": "fr", "ai_model": "ignored-model"})
            self.assertIsNone(self.settings.get()["language"])
            self.settings.update({"ai_base_url": "http://localhost:11434/v1/", "language": "de"})
        self.assertEqual(self.settings.get()["ai_model"], "saved-model")
        self.assertEqual(self.settings.get()["language"], "de")

    def test_invalid_environment_url_does_not_expose_its_credentials(self):
        with patch.dict(os.environ, {"CASTWELL_AI_BASE_URL": "https://provider.example/v1?secret=private"}):
            with self.assertRaises(ValueError) as error:
                self.settings.get()
            self.assertNotIn("private", str(error.exception))
            diagnosis = self.settings.diagnostics()
            self.assertFalse(diagnosis["ready"])
            self.assertNotIn("private", json.dumps(diagnosis))
            self.assertEqual(diagnosis["checks"][-1]["id"], "settings")

    def test_diagnostics_are_read_only_and_require_the_selected_complete_model(self):
        selected = self.root / "local-model"
        self.settings.update({"transcription_model": str(selected)})
        self.install_model(selected)
        with patch("castwell.config.shutil.which", return_value="/usr/bin/fixture"), \
                patch("castwell.config.importlib.util.find_spec", return_value=object()), \
                patch("requests.get", side_effect=AssertionError("Diagnostics must stay offline")), \
                patch("requests.post", side_effect=AssertionError("Diagnostics must stay offline")):
            before = sorted(str(path) for path in self.root.rglob("*"))
            diagnosis = self.settings.diagnostics()
            self.assertTrue(diagnosis["ready"])
            self.assertEqual(before, sorted(str(path) for path in self.root.rglob("*")))
            (selected / "tokenizer.json").unlink()
            self.assertFalse(self.settings.diagnostics()["ready"])
            (selected / "tokenizer.json").touch()
            self.assertFalse(self.settings.diagnostics()["ready"])

    def test_cache_readiness_uses_the_selected_repository_and_main_snapshot(self):
        fake_utils = types.ModuleType("faster_whisper.utils")
        fake_utils._MODELS = {"base": "Example/base", "small": "Example/small"}
        cache = self.root / "models"
        for name in ("base", "small"):
            repository = cache / f"models--Example--{name}"
            (repository / "refs").mkdir(parents=True)
            (repository / "refs" / "main").write_text("revision")
        self.install_model(cache / "models--Example--small" / "snapshots" / "revision")
        with patch.dict("sys.modules", {"faster_whisper.utils": fake_utils}):
            self.assertIsNone(config._model_directory(self.settings.get()))
            directory = cache / "models--Example--base" / "snapshots" / "revision"
            self.install_model(directory)
            self.assertEqual(config._model_directory(self.settings.get()), directory)
            (cache / "models--Example--base" / "refs" / "main").write_text("../small")
            self.assertIsNone(config._model_directory(self.settings.get()))

    def test_diagnostics_distinguish_incomplete_ai_configuration(self):
        self.settings.update({"ai_model": "a-model"})
        diagnosis = self.settings.diagnostics()
        check = next(item for item in diagnosis["checks"] if item["id"] == "classifier")
        self.assertEqual(check["status"], "error")
        self.assertFalse(diagnosis["ready"])
        self.settings.update({"detector": "heuristic"})
        diagnosis = self.settings.diagnostics()
        check = next(item for item in diagnosis["checks"] if item["id"] == "classifier")
        self.assertEqual(check["status"], "warning")

    def test_classifier_test_uses_only_a_synthetic_sample_and_never_returns_secrets(self):
        self.settings.update({"ai_base_url": "http://localhost:11434/v1", "ai_model": "model"})
        with patch.dict(os.environ, {"CASTWELL_AI_KEY": "private-secret"}), \
                patch("castwell.processing.detect_ads", return_value=[]) as detect:
            result = self.settings.test_classifier()
            self.assertTrue(result["ok"])
            self.assertEqual(result["detected_ads"], 0)
            args, kwargs = detect.call_args
            self.assertEqual(args[0]["segments"][1]["text"].split()[5:7], ["Example", "Coffee."])
            self.assertEqual(kwargs["detector"], "ai")
            self.assertNotIn("private-secret", json.dumps(kwargs))
            self.assertNotIn("private-secret", json.dumps(result))
        with patch("castwell.processing.detect_ads", side_effect=RuntimeError("Authorization: private-secret")):
            result = self.settings.test_classifier()
            self.assertFalse(result["ok"])
            self.assertNotIn("private-secret", json.dumps(result))

    def test_classifier_test_rejects_missing_configuration_without_request(self):
        with patch("castwell.processing.detect_ads") as detect:
            self.assertFalse(self.settings.test_classifier()["ok"])
            detect.assert_not_called()

    def test_model_preparation_uses_the_configured_cache_and_reports_progress(self):
        calls = []
        fake_whisper = types.ModuleType("faster_whisper")
        fake_whisper.WhisperModel = lambda *args, **kwargs: calls.append((args, kwargs))
        progress = []
        with patch.dict("sys.modules", {"faster_whisper": fake_whisper}):
            result = config.warm_transcription_model(self.settings, progress.append)
        self.assertTrue(result["ok"])
        self.assertEqual(calls[0][0], ("base",))
        self.assertEqual(calls[0][1]["download_root"], str(self.root / "models"))
        self.assertEqual(calls[0][1]["compute_type"], "int8")
        self.assertEqual(len(progress), 2)

    def test_model_preparation_error_is_safe(self):
        fake_whisper = types.ModuleType("faster_whisper")

        def fail(*args, **kwargs):
            raise RuntimeError("https://provider.example?secret=private")

        fake_whisper.WhisperModel = fail
        with patch.dict("sys.modules", {"faster_whisper": fake_whisper}), self.assertRaises(ProcessingError) as error:
            config.warm_transcription_model(self.settings)
        self.assertNotIn("private", str(error.exception))


if __name__ == "__main__":
    unittest.main()
