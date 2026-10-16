"""Exercise launcher interpreter selection without starting Windows processes."""

import io
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import windows_launcher as launcher


class WindowsLauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="windows launcher ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "extracted app"
        self.root.mkdir()
        self.state_base = Path(self.directory.name) / "local state"
        self.log = self.state_base / "QuestionVolume" / "startup.log"
        self.pythonw = self.root / ".venv" / "Scripts" / "pythonw.exe"
        self.environment = patch.dict(launcher.os.environ, {"LOCALAPPDATA": str(self.state_base)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def installed_environment(self):
        self.pythonw.parent.mkdir(parents=True)
        self.pythonw.touch()

    def test_uses_sibling_environment_module_and_cwd_without_waiting(self):
        self.installed_environment()
        child = Mock()

        def spawn(command, **kwargs):
            self.assertEqual(command, [str(self.pythonw), "-m", "question_volume", "--stop"])
            self.assertEqual(kwargs["cwd"], self.root)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
            self.assertEqual(kwargs["creationflags"], 0x08000000)
            self.assertIs(kwargs["close_fds"], True)
            self.assertEqual(Path(kwargs["stdout"].name), self.log)
            self.assertIn("a", kwargs["stdout"].mode)
            kwargs["stdout"].write(b"ModuleNotFoundError: No module named 'example'\n")
            kwargs["stdout"].flush()
            return child

        with patch.object(launcher.sys, "platform", "win32"), \
                patch.object(launcher.subprocess, "Popen", side_effect=spawn) as popen:
            self.assertEqual(launcher.launch(("--stop",), root=self.root), 0)
        popen.assert_called_once()
        child.wait.assert_not_called()
        child.communicate.assert_not_called()
        self.assertIn("ModuleNotFoundError", self.log.read_text(encoding="utf-8"))

    def test_existing_startup_log_is_appended_and_no_arguments_are_invented(self):
        self.installed_environment()
        self.log.parent.mkdir(parents=True)
        self.log.write_text("previous failure\n", encoding="utf-8")
        with patch.object(launcher.subprocess, "Popen") as popen:
            self.assertEqual(launcher.launch(root=self.root), 0)
        self.assertEqual(popen.call_args.args[0], [str(self.pythonw), "-m", "question_volume"])
        self.assertTrue(self.log.read_text(encoding="utf-8").startswith("previous failure\n"))

    def test_missing_venv_is_reported_without_using_global_python(self):
        errors = io.StringIO()
        with patch.object(launcher.subprocess, "Popen") as popen, \
                patch.object(launcher.sys, "stderr", errors):
            self.assertEqual(launcher.launch(root=self.root), 1)
        popen.assert_not_called()
        self.assertIn("run setup.cmd", errors.getvalue())
        self.assertIn("run setup.cmd", self.log.read_text(encoding="utf-8"))

    def test_spawn_failure_is_logged_and_has_nonzero_result(self):
        self.installed_environment()
        errors = io.StringIO()
        with patch.object(launcher.subprocess, "Popen", side_effect=PermissionError("denied")), \
                patch.object(launcher.sys, "stderr", errors):
            self.assertEqual(launcher.launch(root=self.root), 1)
        self.assertIn("PermissionError", errors.getvalue())
        self.assertIn("PermissionError", self.log.read_text(encoding="utf-8"))

    def test_pythonw_failure_reporting_tolerates_absent_stderr(self):
        with patch.object(launcher.sys, "stderr", None), \
                patch.object(launcher.subprocess, "Popen") as popen:
            self.assertEqual(launcher.launch(root=self.root), 1)
        popen.assert_not_called()
        self.assertIn("run setup.cmd", self.log.read_text(encoding="utf-8"))

    def test_missing_localappdata_reports_error_and_never_spawns(self):
        self.installed_environment()
        errors = io.StringIO()
        with patch.dict(launcher.os.environ, {}, clear=True), \
                patch.object(launcher.sys, "stderr", errors), \
                patch.object(launcher.subprocess, "Popen") as popen:
            self.assertEqual(launcher.launch(root=self.root), 1)
        popen.assert_not_called()
        self.assertIn("LOCALAPPDATA", errors.getvalue())

    def test_unwritable_startup_log_reports_error_and_never_spawns(self):
        self.installed_environment()
        self.state_base.touch()
        errors = io.StringIO()
        with patch.object(launcher.sys, "stderr", errors), \
                patch.object(launcher.subprocess, "Popen") as popen:
            self.assertEqual(launcher.launch(root=self.root), 1)
        popen.assert_not_called()
        self.assertIn("startup.log", errors.getvalue())

    def test_pyw_entry_points_only_delegate_to_launcher(self):
        root = Path(launcher.__file__).resolve().parent
        for name, arguments in (("start", ()), ("stop", (("--stop",),))):
            with self.subTest(name=name), patch.object(launcher, "launch", return_value=7) as launch:
                with self.assertRaises(SystemExit) as exit_result:
                    runpy.run_path(str(root / (name + ".pyw")), run_name="__main__")
                self.assertEqual(exit_result.exception.code, 7)
                launch.assert_called_once_with(*arguments)


if __name__ == "__main__":
    unittest.main()
