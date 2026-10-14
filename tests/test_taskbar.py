"""Native taskbar protocol and lifecycle checks, without a Windows desktop."""

import ctypes
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from question_volume import taskbar
from question_volume.taskbar import WindowsTaskbarDisplay


class FakeNative:
    def __init__(self):
        self.messages = queue.Queue()
        self.titles = []
        self.created = threading.Event()
        self.updated = threading.Event()
        self.closed = threading.Event()
        self.owner = None
        self.operations = []

    def create(self, title, on_update):
        self.owner = threading.get_ident()
        self.on_update = on_update
        self.titles.append(title)
        self.created.set()

    def run(self):
        while self.messages.get(timeout=3) != "stop":
            self.on_update()

    def wake(self):
        self.messages.put("wake")

    def set_title(self, text):
        self.operations.append(threading.get_ident())
        self.titles.append(text)
        self.updated.set()

    def stop(self):
        self.operations.append(threading.get_ident())
        self.messages.put("stop")

    def close(self):
        self.operations.append(threading.get_ident())
        self.closed.set()


class TaskbarLifecycleTests(unittest.TestCase):
    def display(self, native=None, **kwargs):
        native = native or FakeNative()
        display = WindowsTaskbarDisplay(_native_factory=lambda: native, **kwargs)
        self.addCleanup(display.close)
        return display, native

    def test_requires_windows_without_injected_native(self):
        with patch.object(taskbar.sys, "platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "Windows 10/11"):
                WindowsTaskbarDisplay()

    def test_start_show_complete_unicode_and_close_on_own_thread(self):
        display, native = self.display()
        self.assertIs(display.start(), display)
        text = "The exact fill-in answer is: αβγ = 42; 日本語 " * 30
        display.show(text)
        self.assertTrue(native.updated.wait(2))
        self.assertEqual(native.titles, ["?", text])
        display.check()
        display.close()
        self.assertTrue(native.closed.is_set())
        self.assertFalse(display._thread.is_alive())
        self.assertNotEqual(native.owner, threading.get_ident())
        self.assertTrue(all(owner == native.owner for owner in native.operations))

    def test_choice_numbers_and_loading_status_are_passed_without_reformatting(self):
        display, native = self.display()
        display.start()
        for text in ("1", "1,4,6", "Reading…", "?"):
            native.updated.clear()
            if text == "Reading…":
                display.clear(text)
            elif text == "?":
                display.clear()
            else:
                display.show(text)
            self.assertTrue(native.updated.wait(2))
            self.assertEqual(native.titles[-1], text)

    def test_start_and_close_are_idempotent_but_closed_display_cannot_restart(self):
        display, native = self.display()
        self.assertIs(display.start().start(), display)
        self.assertEqual(native.titles, ["?"])
        display.close()
        display.close()
        for operation in (display.start, display.check, lambda: display.show("1")):
            with self.assertRaisesRegex(RuntimeError, "closed"):
                operation()

    def test_show_before_start_and_invalid_caption_are_rejected(self):
        display, _ = self.display()
        with self.assertRaisesRegex(RuntimeError, "start"):
            display.show("1")
        for value in (None, "", "a\x00b", 1):
            with self.assertRaises(ValueError):
                display.show(value)
        with self.assertRaises(ValueError):
            WindowsTaskbarDisplay(_native_factory=FakeNative, ready_text="")
        with self.assertRaises(ValueError):
            WindowsTaskbarDisplay(_native_factory=FakeNative, startup_timeout=0)

    def test_native_creation_failure_is_reported_and_resources_are_closed(self):
        native = FakeNative()
        native.create = Mock(side_effect=OSError("Cannot create window"))
        display, _ = self.display(native)
        with self.assertRaisesRegex(RuntimeError, "Cannot create window"):
            display.start()
        display.close()
        self.assertTrue(native.closed.is_set())

    def test_ui_failure_is_visible_to_check_and_still_closes(self):
        native = FakeNative()
        native.set_title = Mock(side_effect=OSError("Caption update failed"))
        display, _ = self.display(native)
        display.start().show("1")
        self.assertTrue(native.closed.wait(2))
        with self.assertRaisesRegex(RuntimeError, "Caption update failed"):
            display.check()

    def test_startup_timeout_requests_stop_when_creation_eventually_completes(self):
        native = FakeNative()
        original_create = native.create
        release = threading.Event()

        def slow_create(*args):
            release.wait(2)
            original_create(*args)

        native.create = slow_create
        display, _ = self.display(native, startup_timeout=0.01, shutdown_timeout=0.01)
        try:
            with self.assertRaisesRegex(RuntimeError, "did not start"):
                display.start()
        finally:
            release.set()
        self.assertTrue(native.closed.wait(2))
        display.close()

    def test_context_manager_closes_after_exception(self):
        display, native = self.display()
        with self.assertRaisesRegex(ValueError, "test"):
            with display:
                raise ValueError("test")
        self.assertTrue(native.closed.is_set())

    def test_close_called_from_ui_thread_does_not_join_itself(self):
        display, native = self.display()
        original_set_title = native.set_title

        def close_on_ui(text):
            original_set_title(text)
            display.close()

        native.set_title = close_on_ui
        display.start().show("done")
        self.assertTrue(native.closed.wait(2))
        display.close()


class NativeTaskbarMessagesTests(unittest.TestCase):
    def test_native_creation_sets_app_identity_and_nonactivating_minimized_styles(self):
        user32, kernel32, shell32 = Mock(), Mock(), Mock()
        kernel32.GetModuleHandleW.return_value = 456
        shell32.SetCurrentProcessExplicitAppUserModelID.return_value = 0
        user32.LoadIconW.return_value = 789
        user32.RegisterClassExW.return_value = 1
        user32.CreateWindowExW.return_value = 123
        libraries = {"user32": user32, "kernel32": kernel32, "shell32": shell32}
        with patch.object(taskbar.ctypes, "WinDLL", create=True,
                          side_effect=lambda name, **kwargs: libraries[name]):
            native = taskbar._NativeTaskbarWindow()
        native.create("1,4,6", Mock())
        shell32.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with("QuestionTaskbar.Answers")
        arguments = user32.CreateWindowExW.call_args.args
        self.assertEqual(arguments[0], taskbar.WS_EX_APPWINDOW | taskbar.WS_EX_NOACTIVATE)
        self.assertEqual(arguments[2], "1,4,6")
        self.assertTrue(arguments[3] & taskbar.WS_MINIMIZE)
        self.assertEqual(arguments[4:6], (-32000, -32000))
        user32.ShowWindow.assert_called_once_with(123, taskbar.SW_SHOWMINNOACTIVE)
        self.assertIs(user32.CreateWindowExW.restype, ctypes.c_void_p)
        self.assertIs(user32.DefWindowProcW.restype, ctypes.c_ssize_t)
        self.assertIs(kernel32.GetModuleHandleW.restype, ctypes.c_void_p)
        self.assertIsNotNone(native._wndproc)
        native._handle_message(123, taskbar.WM_DESTROY, 0, 0)
        native.close()
        self.assertIsNone(native._wndproc)

    def native(self):
        native = object.__new__(taskbar._NativeTaskbarWindow)
        native.user32 = SimpleNamespace(**{
            name: Mock(return_value=1)
            for name in ("ShowWindow", "DefWindowProcW", "DestroyWindow", "PostQuitMessage",
                         "SetWindowTextW", "PostMessageW", "UnregisterClassW")
        })
        native._hwnd = 123
        native._registered = True
        native._instance = 456
        native._class_name = "test-taskbar"
        native._wndproc = object()
        native._on_update = Mock()
        native._callback_error = None
        return native

    def test_pointer_sized_types_and_exact_win32_struct_layouts(self):
        size = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(ctypes.sizeof(taskbar.WPARAM), size)
        self.assertEqual(ctypes.sizeof(taskbar.LPARAM), size)
        self.assertEqual(ctypes.sizeof(taskbar.LRESULT), size)
        self.assertEqual(ctypes.sizeof(taskbar.POINT), 8)
        self.assertEqual(ctypes.sizeof(taskbar.WNDCLASSEXW), 80 if size == 8 else 48)
        self.assertEqual(ctypes.sizeof(taskbar.WINDOWPOS), 40 if size == 8 else 28)
        self.assertEqual(ctypes.sizeof(taskbar.MSG), 48 if size == 8 else 32)

    def test_taskbar_restore_and_maximize_never_reach_default_handler(self):
        native = self.native()
        for command in (taskbar.SC_RESTORE, taskbar.SC_MAXIMIZE, taskbar.SC_MOVE, taskbar.SC_SIZE):
            self.assertEqual(native._handle_message(123, taskbar.WM_SYSCOMMAND, command | 3, 0), 0)
        native.user32.DefWindowProcW.assert_not_called()
        native.user32.ShowWindow.assert_not_called()

    def test_direct_restore_is_minimized_without_activation(self):
        native = self.native()
        native._handle_message(123, taskbar.WM_SIZE, 0, 0)
        native.user32.ShowWindow.assert_called_once_with(123, taskbar.SW_SHOWMINNOACTIVE)
        native.user32.ShowWindow.reset_mock()
        native._handle_message(123, taskbar.WM_SIZE, taskbar.SIZE_MINIMIZED, 0)
        native.user32.ShowWindow.assert_not_called()
        self.assertEqual(native._handle_message(123, taskbar.WM_MOUSEACTIVATE, 0, 0), taskbar.MA_NOACTIVATE)

    def test_external_position_changes_are_kept_offscreen_and_nonactivating(self):
        native = self.native()
        position = taskbar.WINDOWPOS()
        position.x, position.y = 10, 20
        position.flags = taskbar.SWP_NOMOVE
        native._handle_message(123, taskbar.WM_WINDOWPOSCHANGING, 0, ctypes.addressof(position))
        self.assertEqual((position.x, position.y), (-32000, -32000))
        self.assertTrue(position.flags & taskbar.SWP_NOACTIVATE)
        self.assertFalse(position.flags & taskbar.SWP_NOMOVE)

    def test_native_title_setter_preserves_full_unicode(self):
        native = self.native()
        text = "Answer 日本語 café 🧬" * 100
        native.set_title(text)
        native.user32.SetWindowTextW.assert_called_once_with(123, text)

    def test_update_is_dispatched_and_callback_failures_do_not_cross_ctypes(self):
        native = self.native()
        self.assertEqual(native._window_proc(123, taskbar.WM_APP_UPDATE, 0, 0), 0)
        native._on_update.assert_called_once_with()
        error = RuntimeError("write failed")
        native._on_update.side_effect = error
        self.assertEqual(native._window_proc(123, taskbar.WM_APP_UPDATE, 0, 0), 0)
        self.assertIs(native._callback_error, error)
        native.user32.DestroyWindow.assert_called_once_with(123)
        native.user32.PostQuitMessage.assert_called_once_with(1)

    def test_wake_posts_asynchronous_message_and_destroy_posts_quit(self):
        native = self.native()
        native.wake()
        native.user32.PostMessageW.assert_called_once_with(123, taskbar.WM_APP_UPDATE, 0, 0)
        native._handle_message(123, taskbar.WM_CLOSE, 0, 0)
        native.user32.DestroyWindow.assert_called_once_with(123)
        native._handle_message(123, taskbar.WM_DESTROY, 0, 0)
        self.assertIsNone(native._hwnd)
        native.user32.PostQuitMessage.assert_called_once_with(0)
        native.wake()
        self.assertEqual(native.user32.PostMessageW.call_count, 1)

    def test_cleanup_unregisters_before_releasing_pinned_callback(self):
        native = self.native()
        callback = native._wndproc

        def unregister(*args):
            self.assertIs(native._wndproc, callback)
            return 1

        native.user32.UnregisterClassW.side_effect = unregister
        native._hwnd = None
        native.close()
        native.user32.UnregisterClassW.assert_called_once_with("test-taskbar", 456)
        self.assertFalse(native._registered)
        self.assertIsNone(native._wndproc)
        native.close()
        self.assertEqual(native.user32.UnregisterClassW.call_count, 1)


if __name__ == "__main__":
    unittest.main()
