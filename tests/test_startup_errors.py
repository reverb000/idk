"""Startup errors must identify missing modules without exposing other payloads."""

from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from question_volume import app
from question_volume.config import Settings


class StartupErrorTests(unittest.TestCase):
    def test_missing_module_is_named_in_log_and_console(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(app.sys, "platform", "win32"))
            stack.enter_context(patch.object(app, "state_directory", return_value=Path(directory)))
            stack.enter_context(patch.object(app, "configure_logging"))
            stack.enter_context(patch.object(app, "enable_dpi_awareness"))
            stack.enter_context(patch.object(app.Settings, "load", return_value=Settings()))
            stack.enter_context(patch.object(app, "check", side_effect=ModuleNotFoundError(
                "private exception payload", name="pytesseract",
            )))
            printed = stack.enter_context(patch("builtins.print"))
            with self.assertLogs("question_volume", level="ERROR") as logs:
                self.assertEqual(app.main(["--check"]), 1)
        message = "\n".join(logs.output)
        self.assertIn("Missing Python module 'pytesseract'", message)
        self.assertIn("repair.cmd", message)
        self.assertIn("diagnose.cmd", message)
        self.assertNotIn("private exception payload", message)
        self.assertIn("pytesseract", printed.call_args.args[0])

    def test_untrusted_module_name_is_not_logged(self):
        for name in (None, "private\nscreen text", "secret-token", "", "α"):
            with self.subTest(name=name):
                message = app.missing_module_message(ModuleNotFoundError("private payload", name=name))
                self.assertIn("module 'unknown'", message)
                self.assertNotIn("private", message)
                self.assertNotIn("secret-token", message)

    def test_dotted_dependency_names_are_preserved(self):
        message = app.missing_module_message(ModuleNotFoundError(name="PIL._imaging"))
        self.assertIn("'PIL._imaging'", message)


class PrerequisiteDependencyTests(unittest.TestCase):
    def test_check_fails_for_capture_or_listener_dependencies_before_claiming_success(self):
        for missing in ("mss", "PIL.Image", "pynput.mouse"):
            with self.subTest(module=missing):
                def import_module(name):
                    if name == missing:
                        raise ModuleNotFoundError(name=name)
                    return Mock()

                with patch("builtins.print") as printed, \
                     patch.object(app.importlib, "import_module", side_effect=import_module), \
                     patch.object(app, "initialize") as initialize:
                    with self.assertRaises(ModuleNotFoundError):
                        app.check(Settings())
                initialize.assert_not_called()
                printed.assert_not_called()

    def test_complete_dependency_check_still_starts_and_closes_display(self):
        display = Mock()
        with patch("builtins.print"), patch.object(app.importlib, "import_module"), \
             patch.object(app, "initialize", return_value=(Mock(), Mock(), display)):
            app.check(Settings())
        display.start.assert_called_once_with()
        display.check.assert_called_once_with()
        display.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
