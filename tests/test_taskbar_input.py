from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from question_volume import taskbar_input


class TaskbarInputTests(unittest.TestCase):
    def inspector(self, classes, window=21, root=10):
        tester = taskbar_input._HitTester.__new__(taskbar_input._HitTester)
        def class_name(handle, buffer, count):
            buffer.value = classes.get(handle, "")
            return len(buffer.value)
        tester.user = SimpleNamespace(WindowFromPoint=Mock(return_value=window),
                                      GetAncestor=Mock(return_value=root),
                                      GetClassNameW=Mock(side_effect=class_name))
        return tester

    def test_taskbar_children_and_other_monitor_taskbars_keep_native_clicks(self):
        for name in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            tester = self.inspector({10: name, 21: "Button"})
            self.assertTrue(tester.contains(-500, 1050))
            point = tester.user.WindowFromPoint.call_args.args[0]
            self.assertEqual((point.x, point.y), (-500, 1050))

    def test_clickthrough_overlay_and_regular_app_classification(self):
        self.assertTrue(self.inspector({10: "QuestionTaskbarOverlay_123", 21: "Child"}).contains(100, 100))
        self.assertFalse(self.inspector({10: "Chrome_WidgetWin_1", 21: "Chrome_RenderWidgetHostHWND"}).contains(100, 100))

    def test_missing_native_data_preserves_input(self):
        self.assertIsNone(self.inspector({}, window=0).contains(1, 1))
        self.assertIsNone(self.inspector({}, root=0).contains(1, 1))
        self.assertIsNone(self.inspector({}).contains(1, 1))

    def test_public_helper_handles_native_errors_without_blocking_the_hook(self):
        with patch.object(taskbar_input.sys, "platform", "win32"), \
             patch.object(taskbar_input, "_hit_tester", side_effect=OSError):
            self.assertIsNone(taskbar_input.taskbar_point(1, 2))


if __name__ == "__main__":
    unittest.main()
