import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from question_volume.app import check_solver, initialize, main
from question_volume.config import Settings, configure_solver


class LocalConfigurationTests(unittest.TestCase):
    def test_default_is_local_with_cpu_friendly_timeout(self):
        settings = Settings.from_dict({})
        self.assertEqual((settings.backend, settings.model, settings.api_timeout_seconds),
                         ("ollama", "qwen2.5:7b", 180))

    def test_switch_backend_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"direction":"down","capture_width":900}', encoding="utf-8")
            configure_solver(path, "ollama", "qwen2.5:3b")
            settings = Settings.load(path)
            self.assertEqual(settings.direction, "down")
            self.assertEqual(settings.capture_width, 900)
            self.assertEqual(settings.model, "qwen2.5:3b")
            configure_solver(path, "ollama", "qwen2.5:3b")
            self.assertEqual(settings, Settings.load(path))

    def test_invalid_configuration_does_not_overwrite_user_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = '{"direction":"sideways"}'
            path.write_text(original, encoding="utf-8")
            with self.assertRaises(ValueError):
                configure_solver(path, "ollama", "qwen2.5:7b")
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_configuration_cli_works_without_desktop_or_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            with patch("builtins.print"):
                self.assertEqual(main(["--config", str(path), "--configure-backend", "ollama"]), 0)
            self.assertEqual(json.loads(path.read_text())["model"], "qwen2.5:7b")

    def test_unknown_backend_rejected(self):
        with self.assertRaises(ValueError):
            Settings.from_dict({"backend": "missing"})


class LocalInitializationTests(unittest.TestCase):
    def test_local_backend_never_reads_an_api_key(self):
        with patch("question_volume.app.find_tesseract", return_value="/local/tesseract"), \
             patch("pytesseract.pytesseract.tesseract_cmd", "tesseract"), \
             patch("pytesseract.get_tesseract_version"), \
             patch("pytesseract.get_languages", return_value=["eng"]), \
             patch("question_volume.app.api_key", side_effect=AssertionError("Must not read cloud credentials")), \
             patch("question_volume.app.WindowsVolume"), \
             patch("question_volume.local_solver.OllamaAnswerSolver") as solver:
            _, selected, _ = initialize(Settings())
            self.assertIs(selected, solver.return_value)
            solver.assert_called_once_with("qwen2.5:7b", timeout=180)
            selected.check_model.assert_called_once()

    def test_known_answer_check_does_not_change_volume(self):
        solver, volume = Mock(), Mock()
        solver.solve.return_value = "B"
        with patch("question_volume.app.initialize", return_value=(Mock(), solver, volume)), patch("builtins.print"):
            check_solver(Settings())
        self.assertEqual(solver.solve.call_args.args[0].choices[1].text, "4")
        volume.signal.assert_not_called()

    def test_known_answer_check_detects_wrong_response(self):
        solver = Mock()
        solver.solve.return_value = "D"
        with patch("question_volume.app.initialize", return_value=(Mock(), solver, Mock())):
            with self.assertRaises(RuntimeError):
                check_solver(Settings())


if __name__ == "__main__":
    unittest.main()
