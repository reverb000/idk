"""Check installation decisions without installing packages or using Windows COM."""

from pathlib import Path
import subprocess
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import install_windows as installer


class WindowsInstallerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="windows installer ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ("requirements.txt", "start.pyw", "stop.pyw"):
            (self.root / name).touch()
        self.python = self.root / ".venv" / "Scripts" / "python.exe"

    def existing_environment(self):
        self.python.parent.mkdir(parents=True)
        self.python.touch()
        return self.python

    def test_rejects_non_windows_before_creating_environment(self):
        with patch.object(installer.sys, "platform", "linux"), \
             patch.object(installer, "install") as install, \
             patch("builtins.print"):
            self.assertEqual(installer.main([]), 1)
        install.assert_not_called()

    def test_unsupported_python_is_rejected(self):
        with patch.object(installer.sys, "platform", "win32"), \
             patch.object(installer.sys, "version_info", (3, 15, 0)):
            with self.assertRaisesRegex(RuntimeError, "Python 3.14"):
                installer.check_runtime()

    def test_creates_new_environment_with_current_interpreter(self):
        def create(path):
            self.assertEqual(path, self.root / ".venv")
            self.existing_environment()

        with patch.object(installer.venv, "EnvBuilder") as builder, patch("builtins.print"):
            builder.return_value.create.side_effect = create
            self.assertEqual(installer.prepare_environment(self.root), self.python)
        builder.assert_called_once_with(with_pip=True)

    def test_existing_same_minor_environment_is_reused_without_recreating_it(self):
        self.existing_environment()
        sentinel = self.python.parent / "user-file.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with patch.object(installer.sys, "version_info", (3, 14, 8)), \
             patch.object(installer.subprocess, "run", return_value=SimpleNamespace(stdout="3.14\n")) as run, \
             patch.object(installer.venv, "EnvBuilder") as builder, patch("builtins.print"):
            self.assertEqual(installer.prepare_environment(self.root), self.python)
        builder.assert_not_called()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        run.assert_called_once_with(
            [str(self.python), "-c", installer.VERSION_PROBE], check=True,
            cwd=self.root, capture_output=True, text=True,
        )

    def test_existing_different_minor_is_left_untouched(self):
        self.existing_environment()
        with patch.object(installer.sys, "version_info", (3, 14, 8)), \
             patch.object(installer.subprocess, "run", return_value=SimpleNamespace(stdout="3.12\n")), \
             patch.object(installer.venv, "EnvBuilder") as builder:
            with self.assertRaisesRegex(RuntimeError, "py -3.12"):
                installer.prepare_environment(self.root)
        builder.assert_not_called()
        self.assertTrue(self.python.is_file())

    def test_incomplete_existing_environment_is_not_repaired_or_removed(self):
        (self.root / ".venv").mkdir()
        with patch.object(installer.venv, "EnvBuilder") as builder:
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                installer.prepare_environment(self.root)
        builder.assert_not_called()

    def test_unrecognized_or_failed_existing_python_blocks_installation(self):
        self.existing_environment()
        with patch.object(installer.subprocess, "run", return_value=SimpleNamespace(stdout="unexpected output")):
            with self.assertRaisesRegex(RuntimeError, "Could not identify"):
                installer.prepare_environment(self.root)
        with patch.object(installer.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "probe")):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.prepare_environment(self.root)

    def test_local_install_preserves_preferences_and_never_requests_cloud_key(self):
        config = self.root / "config.json"
        original = '{"capture_width":900,"direction":"down"}'
        config.write_text(original, encoding="utf-8")
        with patch.object(installer, "prepare_environment", return_value=self.python), \
             patch.object(installer.subprocess, "run") as run, patch("builtins.print"):
            installer.install(self.root, "ollama", None)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands, [
            [str(self.python), "-m", "pip", "install", "-r", "requirements.txt"],
            [str(self.python), "-m", "question_volume", "--configure-backend", "ollama", "--model", "qwen2.5:7b"],
            [str(self.python), "-m", "question_volume", "--check"],
            [str(self.python), str(self.root / "install_windows.py"), "--create-shortcuts"],
        ])
        self.assertEqual(config.read_text(encoding="utf-8"), original)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs, {"check": True, "cwd": self.root})
        self.assertFalse(any("powershell" in str(command).lower() for command in commands))

    def test_openai_configuration_requests_key_only_after_selecting_cloud_backend(self):
        with patch.object(installer, "prepare_environment", return_value=self.python), \
             patch.object(installer, "run_step") as run, patch("builtins.print"):
            installer.install(self.root, "openai", "custom-model")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[1][-4:], ["--configure-backend", "openai", "--model", "custom-model"])
        self.assertEqual(commands[2], [str(self.python), "-m", "question_volume", "--set-api-key"])

    def test_install_failure_stops_before_configuring_user_settings(self):
        with patch.object(installer, "prepare_environment", return_value=self.python), \
             patch.object(installer, "run_step", side_effect=subprocess.CalledProcessError(1, "pip")) as run, \
             patch("builtins.print"):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.install(self.root, "ollama", None)
        self.assertEqual(run.call_count, 1)

    def test_shortcuts_use_pythonw_and_preserve_existing_links(self):
        self.existing_environment()
        (self.python.parent / "pythonw.exe").touch()
        (self.root / "start.lnk").write_text("custom shortcut", encoding="utf-8")
        shell = Mock()
        comtypes = ModuleType("comtypes")
        client = ModuleType("comtypes.client")
        client.CreateObject = Mock(return_value=shell)
        with patch.dict("sys.modules", {"comtypes": comtypes, "comtypes.client": client}), \
             patch("builtins.print"):
            installer.create_shortcuts(self.root)
        client.CreateObject.assert_called_once_with("WScript.Shell", dynamic=True)
        shell.CreateShortcut.assert_called_once_with(str(self.root / "stop.lnk"))
        shortcut = shell.CreateShortcut.return_value
        self.assertEqual(shortcut.TargetPath, str(self.python.parent / "pythonw.exe"))
        self.assertEqual(shortcut.Arguments, f'"{self.root / "stop.pyw"}"')
        self.assertEqual(shortcut.WorkingDirectory, str(self.root))
        shortcut.Save.assert_called_once_with()
        self.assertEqual((self.root / "start.lnk").read_text(encoding="utf-8"), "custom shortcut")

    def test_missing_extracted_files_stops_before_environment_changes(self):
        (self.root / "requirements.txt").unlink()
        with patch.object(installer, "prepare_environment") as prepare:
            with self.assertRaisesRegex(RuntimeError, "Extract the whole program"):
                installer.install(self.root, "ollama", None)
        prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
