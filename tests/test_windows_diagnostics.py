"""Exercise diagnostics without native Windows APIs or installed packages."""

import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import diagnose_windows as diagnostics


class WindowsDiagnosticsTests(unittest.TestCase):
    def run_diagnostics(self, importer, platform="win32"):
        output = io.StringIO()
        with patch.object(diagnostics.sys, "platform", platform), \
             patch.object(diagnostics.importlib, "import_module", side_effect=importer), \
             contextlib.redirect_stdout(output):
            result = diagnostics.main()
        return result, output.getvalue()

    def test_non_windows_does_not_import_native_dependencies(self):
        importer = Mock()
        result, output = self.run_diagnostics(importer, "linux")
        self.assertEqual(result, 1)
        importer.assert_not_called()
        self.assertIn("Run diagnose.cmd on the Windows", output)
        self.assertIn(str(diagnostics.ROOT), output)

    def test_dpi_is_initialized_before_probes_and_check_runs_last(self):
        calls = []
        app_main = Mock(return_value=0)

        def importer(name):
            calls.append(name)
            if name == "question_volume.runtime":
                return SimpleNamespace(enable_dpi_awareness=lambda: calls.append("dpi"))
            if name == "question_volume.app":
                return SimpleNamespace(main=app_main)
            return SimpleNamespace()

        result, output = self.run_diagnostics(importer)
        self.assertEqual(result, 0)
        self.assertEqual(calls, ["question_volume.runtime", "dpi", *diagnostics.REQUIRED_MODULES,
                                 "question_volume.app"])
        app_main.assert_called_once_with(["--check"])
        self.assertIn("Installation check passed", output)

    def test_missing_dependency_names_transitive_module_and_skips_app(self):
        calls = []

        def importer(name):
            calls.append(name)
            if name == "question_volume.runtime":
                return SimpleNamespace(enable_dpi_awareness=Mock())
            if name == "comtypes.client":
                raise ModuleNotFoundError("sensitive arbitrary message", name="_ctypes")
            return SimpleNamespace()

        result, output = self.run_diagnostics(importer)
        self.assertEqual(result, 1)
        self.assertIn("missing module '_ctypes'", output)
        self.assertNotIn("sensitive", output)
        self.assertNotIn("question_volume.app", calls)
        self.assertIn("--force-reinstall", output)
        self.assertIn("OK import keyring.backends.Windows", output)

    def test_arbitrary_import_error_message_is_not_printed(self):
        def importer(name):
            if name == "question_volume.runtime":
                return SimpleNamespace(enable_dpi_awareness=Mock())
            raise ImportError("secret API key and screen contents")

        result, output = self.run_diagnostics(importer)
        self.assertEqual(result, 1)
        self.assertIn("ImportError", output)
        self.assertNotIn("secret", output)
        self.assertNotIn("--force-reinstall", output)

    def test_unsafe_missing_module_name_is_not_printed(self):
        error = ModuleNotFoundError("private", name="PIL\nprivate screen text")
        self.assertEqual(diagnostics.module_name(error), "unknown")
        self.assertEqual(diagnostics.module_name(ModuleNotFoundError()), "unknown")

    def test_powershell_repair_command_quotes_spaces_and_apostrophes(self):
        root = Path("folder with spaces/user's program")
        command = diagnostics.repair_command(root)
        self.assertIn("user''s program", command)
        self.assertTrue(command.startswith("& '"))
        self.assertIn("python.exe' -m pip install --force-reinstall -r '", command)
        self.assertTrue(command.endswith("requirements.txt'"))

    def test_dpi_failure_stops_before_dependency_probes(self):
        runtime = SimpleNamespace(enable_dpi_awareness=Mock(side_effect=OSError("private data")))
        importer = Mock(return_value=runtime)
        result, output = self.run_diagnostics(importer)
        self.assertEqual(result, 1)
        importer.assert_called_once_with("question_volume.runtime")
        self.assertIn("Windows initialization: OSError", output)
        self.assertNotIn("private data", output)

    def test_failed_app_prerequisites_are_not_reported_as_success(self):
        app = SimpleNamespace(main=Mock(return_value=1))

        def importer(name):
            if name == "question_volume.runtime":
                return SimpleNamespace(enable_dpi_awareness=Mock())
            return app

        result, output = self.run_diagnostics(importer)
        self.assertEqual(result, 1)
        self.assertIn("prerequisite error above", output)
        self.assertNotIn("Installation check passed", output)

    def test_late_missing_module_uses_the_same_safe_report(self):
        def importer(name):
            if name == "question_volume.runtime":
                return SimpleNamespace(enable_dpi_awareness=Mock())
            if name == "question_volume.app":
                raise ModuleNotFoundError("private", name="pytesseract")
            return SimpleNamespace()

        result, output = self.run_diagnostics(importer)
        self.assertEqual(result, 1)
        self.assertIn("missing module 'pytesseract'", output)
        self.assertIn("--force-reinstall", output)
        self.assertNotIn("private", output)


if __name__ == "__main__":
    unittest.main()
