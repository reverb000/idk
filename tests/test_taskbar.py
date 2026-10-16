"""Overlay safety, independent scanner ownership, and Win32 protocol checks."""

import ctypes
import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from question_volume import taskbar
from question_volume.taskbar import WindowsTaskbarDisplay
from question_volume.taskbar_layout import Placement, Rect, TaskbarSnapshot


def snapshot(*occupied, observed_at=None, reliable=True, visible=True):
    return TaskbarSnapshot(
        Rect(0, 1000, 1400, 1048), tuple(occupied),
        observed_at=time.monotonic() if observed_at is None else observed_at,
        reliable=reliable, visible=visible,
    )


class FakeInspector:
    def __init__(self):
        self.owner = threading.get_ident()
        self.operations = []
        self.occupied = (Rect(0, 1000, 220, 1048), Rect(600, 1000, 850, 1048),
                         Rect(1120, 1000, 1400, 1048))
        self.observed_at = None
        self.reliable = self.visible = True
        self.error = None
        self.scanned = threading.Event()
        self.closed = threading.Event()

    def scan(self):
        self.operations.append(threading.get_ident())
        self.scanned.set()
        if self.error:
            raise self.error
        return (snapshot(*self.occupied, observed_at=self.observed_at,
                         reliable=self.reliable, visible=self.visible),)

    def close(self):
        self.operations.append(threading.get_ident())
        self.closed.set()


class FakeNative:
    def __init__(self):
        self.messages = queue.Queue()
        self.titles = []
        self.placements = []
        self.created = threading.Event()
        self.updated = threading.Event()
        self.rendered = threading.Event()
        self.closed = threading.Event()
        self.owner = None
        self.operations = []

    def create(self, title, on_update):
        self.owner = threading.get_ident()
        self.on_update = on_update
        self.titles.append(title)
        self.created.set()

    def run(self):
        while True:
            try:
                message = self.messages.get(timeout=0.02)
                if message == "stop":
                    return
            except queue.Empty:
                pass
            self.on_update()

    def wake(self):
        self.messages.put("wake")

    def measure(self, text):
        self.operations.append(threading.get_ident())
        return len(text) * 8, 18

    def set_title(self, text):
        self.operations.append(threading.get_ident())
        self.titles.append(text)
        self.updated.set()

    def render(self, placement):
        self.operations.append(threading.get_ident())
        self.placements.append(placement)
        self.rendered.set()

    def stop(self):
        self.operations.append(threading.get_ident())
        self.messages.put("stop")

    def close(self):
        self.operations.append(threading.get_ident())
        self.closed.set()


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class TaskbarLifecycleTests(unittest.TestCase):
    def display(self, native=None, *, inspector_factory=None, **kwargs):
        native = native or FakeNative()
        inspectors = []

        def create_inspector():
            inspector = inspector_factory() if inspector_factory else FakeInspector()
            inspectors.append(inspector)
            return inspector

        display = WindowsTaskbarDisplay(_native_factory=lambda: native,
                                       _inspector_factory=create_inspector, **kwargs)
        self.addCleanup(display.close)
        return display, native, inspectors

    def test_requires_windows_without_injected_native(self):
        with patch.object(taskbar.sys, "platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "Windows 10/11"):
                WindowsTaskbarDisplay()

    def test_unicode_is_preserved_and_each_thread_owns_its_resources(self):
        display, native, inspectors = self.display()
        self.assertIs(display.start(), display)
        text = "The exact fill-in answer is: αβγ = 42; 日本語 🧬 " * 30
        display.show(text)
        self.assertTrue(native.updated.wait(2))
        self.assertEqual(native.titles, ["?", text])
        display.check()
        display.close()
        inspector = inspectors[0]
        self.assertTrue(native.closed.is_set())
        self.assertTrue(inspector.closed.is_set())
        self.assertFalse(display._thread.is_alive())
        self.assertFalse(display._scanner_thread.is_alive())
        self.assertNotEqual(native.owner, threading.get_ident())
        self.assertNotEqual(inspector.owner, native.owner)
        self.assertTrue(all(owner == native.owner for owner in native.operations))
        self.assertTrue(all(owner == inspector.owner for owner in inspector.operations))

    def test_every_choice_number_and_loading_status_pass_without_reformatting(self):
        display, native, _ = self.display()
        display.start()
        for text in ("1", "1,4,6", "8,12,26", "Reading…", "?"):
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
        display, native, _ = self.display()
        self.assertIs(display.start().start(), display)
        self.assertEqual(native.titles, ["?"])
        display.close()
        display.close()
        for operation in (display.start, display.check, lambda: display.show("1")):
            with self.assertRaisesRegex(RuntimeError, "closed"):
                operation()

    def test_show_before_start_invalid_caption_and_timeout_are_rejected(self):
        display, _, _ = self.display()
        with self.assertRaisesRegex(RuntimeError, "start"):
            display.show("1")
        for value in (None, "", "a\x00b", 1):
            with self.assertRaises(ValueError):
                display.show(value)
        with self.assertRaises(ValueError):
            WindowsTaskbarDisplay(_native_factory=FakeNative, ready_text="")
        with self.assertRaises(ValueError):
            WindowsTaskbarDisplay(_native_factory=FakeNative, startup_timeout=0)

    def test_native_creation_failure_closes_window_and_scanner(self):
        native = FakeNative()
        native.create = Mock(side_effect=OSError("Cannot create window"))
        display, _, inspectors = self.display(native)
        with self.assertRaisesRegex(RuntimeError, "Cannot create window"):
            display.start()
        display.close()
        self.assertTrue(native.closed.is_set())
        self.assertTrue(inspectors[0].closed.is_set())

    def test_inspector_initialization_failure_is_explicit_and_no_window_opens(self):
        def failed():
            raise RuntimeError("UI Automation is unavailable")

        display, native, _ = self.display(inspector_factory=failed)
        with self.assertRaisesRegex(RuntimeError, "inspection could not start.*UI Automation"):
            display.start()
        self.assertFalse(native.created.is_set())

    def test_ui_failure_is_visible_to_check_and_closes_both_threads(self):
        native = FakeNative()
        native.set_title = Mock(side_effect=OSError("Caption update failed"))
        display, _, inspectors = self.display(native)
        display.start().show("1")
        self.assertTrue(native.closed.wait(2))
        self.assertTrue(inspectors[0].closed.wait(2))
        with self.assertRaisesRegex(RuntimeError, "Caption update failed"):
            display.check()

    def test_transient_scanner_failure_hides_then_recovers(self):
        display, native, inspectors = self.display()
        display.start()
        self.assertTrue(wait_for(lambda: native.placements and native.placements[-1] is not None))
        inspector = inspectors[0]
        inspector.error = RuntimeError("Explorer busy")
        self.assertTrue(wait_for(lambda: native.placements[-1] is None))
        display.check()
        inspector.error = None
        self.assertTrue(wait_for(lambda: native.placements[-1] is not None))

    def test_stale_unreliable_autohidden_and_full_taskbar_hide_overlay(self):
        display, native, inspectors = self.display()
        display.start()
        inspector = inspectors[0]
        self.assertTrue(wait_for(lambda: native.placements and native.placements[-1] is not None))
        for name, bad, good in (
            ("observed_at", time.monotonic() - 3, None),
            ("reliable", False, True),
            ("visible", False, True),
            ("occupied", (Rect(0, 1000, 1400, 1048),), inspector.occupied),
        ):
            with self.subTest(name=name):
                setattr(inspector, name, bad)
                self.assertTrue(wait_for(lambda: native.placements[-1] is None))
                setattr(inspector, name, good)
                self.assertTrue(wait_for(lambda: native.placements[-1] is not None))

    def test_inspector_blocked_scan_does_not_block_gui_and_last_snapshot_expires(self):
        release, entered = threading.Event(), threading.Event()

        class BlockingInspector(FakeInspector):
            def scan(self):
                if self.scanned.is_set():
                    entered.set()
                    release.wait(3)
                return super().scan()

        display, native, _ = self.display(inspector_factory=BlockingInspector)
        try:
            display.start()
            self.assertTrue(wait_for(lambda: native.placements and native.placements[-1] is not None))
            self.assertTrue(entered.wait(1))
            display.show("26")
            self.assertTrue(native.updated.wait(0.5))
            self.assertTrue(wait_for(lambda: native.placements[-1] is None, timeout=1.5))
        finally:
            release.set()

    def test_scanner_change_moves_overlay_without_moving_any_control(self):
        display, native, inspectors = self.display()
        display.start()
        self.assertTrue(wait_for(lambda: native.placements and native.placements[-1] is not None))
        original = native.placements[-1].bounds
        inspector = inspectors[0]
        # Fill the original largest empty interval. The remaining safe interval
        # is between x=850 and x=1120; controls retain exactly these rectangles.
        inspector.occupied = (Rect(0, 1000, 850, 1048), Rect(1120, 1000, 1400, 1048))
        expected = inspector.occupied
        self.assertTrue(wait_for(lambda: native.placements[-1] is not None and
                                 native.placements[-1].bounds != original))
        self.assertGreaterEqual(native.placements[-1].bounds.left, 862)
        self.assertLessEqual(native.placements[-1].bounds.right, 1108)
        self.assertEqual(inspector.occupied, expected)

    def test_long_text_selects_marquee_instead_of_truncating_caption(self):
        display, native, _ = self.display()
        display.start()
        text = "Long exact answer 日本語 🧬 " * 100
        display.show(text)
        self.assertTrue(wait_for(lambda: native.placements and native.placements[-1] is not None and
                                 native.placements[-1].scroll))
        self.assertEqual(native.titles[-1], text)
        self.assertLess(native.placements[-1].bounds.right - native.placements[-1].bounds.left,
                        len(text) * 8)

    def test_startup_timeout_stops_when_creation_eventually_completes(self):
        native = FakeNative()
        original_create = native.create
        release = threading.Event()

        def slow_create(*args):
            release.wait(2)
            original_create(*args)

        native.create = slow_create
        display, _, _ = self.display(native, startup_timeout=0.01, shutdown_timeout=0.01)
        try:
            with self.assertRaisesRegex(RuntimeError, "did not start"):
                display.start()
        finally:
            release.set()
        self.assertTrue(native.closed.wait(2))
        display.close()

    def test_context_manager_closes_after_exception(self):
        display, native, inspectors = self.display()
        with self.assertRaisesRegex(ValueError, "test"):
            with display:
                raise ValueError("test")
        self.assertTrue(native.closed.is_set())
        self.assertTrue(inspectors[0].closed.is_set())

    def test_close_called_from_ui_thread_does_not_join_itself(self):
        display, native, _ = self.display()
        original_set_title = native.set_title

        def close_on_ui(text):
            original_set_title(text)
            display.close()

        native.set_title = close_on_ui
        display.start().show("done")
        self.assertTrue(native.closed.wait(2))
        display.close()


class NativeTaskbarMessagesTests(unittest.TestCase):
    def native(self):
        libraries = {name: Mock() for name in ("user32", "kernel32", "gdi32")}
        libraries["kernel32"].GetModuleHandleW.return_value = 456
        libraries["user32"].RegisterClassExW.return_value = 1
        libraries["user32"].CreateWindowExW.return_value = 123
        libraries["user32"].SetTimer.return_value = 1
        libraries["user32"].GetDC.return_value = 321
        libraries["user32"].BeginPaint.return_value = 321
        libraries["gdi32"].CreateFontW.return_value = 789
        libraries["gdi32"].CreateSolidBrush.return_value = 888
        libraries["gdi32"].SaveDC.return_value = 1

        def extent(dc, text, length, output):
            size = ctypes.cast(output, ctypes.POINTER(taskbar.SIZE)).contents
            size.cx, size.cy = length * 8, 18
            return 1

        def client(hwnd, output):
            rect = ctypes.cast(output, ctypes.POINTER(taskbar.RECT)).contents
            rect.left, rect.top, rect.right, rect.bottom = 0, 0, 100, 30
            return 1

        libraries["gdi32"].GetTextExtentPoint32W.side_effect = extent
        libraries["user32"].GetClientRect.side_effect = client
        with patch.object(taskbar.ctypes, "WinDLL", create=True,
                          side_effect=lambda name, **kwargs: libraries[name]):
            native = taskbar._NativeTaskbarWindow()
        native.create("1,4,6", Mock())
        self.addCleanup(native.close)
        return native

    def test_native_is_hidden_popup_toolwindow_without_appbutton_or_icon(self):
        native = self.native()
        arguments = native.user32.CreateWindowExW.call_args.args
        self.assertEqual(arguments[0], taskbar.WS_EX_TOOLWINDOW | taskbar.WS_EX_NOACTIVATE |
                         taskbar.WS_EX_LAYERED | taskbar.WS_EX_TRANSPARENT)
        self.assertFalse(arguments[0] & taskbar.WS_EX_APPWINDOW)
        self.assertEqual(arguments[2], "1,4,6")
        self.assertEqual(arguments[3], taskbar.WS_POPUP)
        self.assertEqual(arguments[4:8], (0, 0, 0, 0))
        native.user32.ShowWindow.assert_not_called()
        native.user32.SetLayeredWindowAttributes.assert_called_once_with(123, 0, 235, taskbar.LWA_ALPHA)
        native.user32.SetTimer.assert_called_once_with(123, taskbar.TIMER_ID, 100, None)
        # Neither Explorer parenting nor application identity/taskbar APIs exist.
        self.assertNotIn("shell32", native.__dict__)
        self.assertIsNotNone(ctypes.cast(native._wndproc, ctypes.c_void_p).value)

    def test_win32_pointer_prototypes_structures_and_byte_array_have_exact_layout(self):
        native = self.native()
        size = ctypes.sizeof(ctypes.c_void_p)
        for scalar in (taskbar.WPARAM, taskbar.LPARAM, taskbar.LRESULT, taskbar.UINT_PTR):
            self.assertEqual(ctypes.sizeof(scalar), size)
        self.assertEqual(ctypes.sizeof(taskbar.POINT), 8)
        self.assertEqual(ctypes.sizeof(taskbar.RECT), 16)
        self.assertEqual(ctypes.sizeof(taskbar.SIZE), 8)
        self.assertEqual(ctypes.sizeof(taskbar.WNDCLASSEXW), 80 if size == 8 else 48)
        self.assertEqual(ctypes.sizeof(taskbar.WINDOWPOS), 40 if size == 8 else 28)
        self.assertEqual(ctypes.sizeof(taskbar.MSG), 48 if size == 8 else 32)
        self.assertEqual(ctypes.sizeof(taskbar.PAINTSTRUCT), 72 if size == 8 else 64)
        self.assertIs(taskbar.PAINTSTRUCT._fields_[-1][1]._type_, ctypes.c_ubyte)
        self.assertIs(native.user32.CreateWindowExW.restype, ctypes.c_void_p)
        self.assertIs(native.user32.SetTimer.restype, ctypes.c_size_t)
        self.assertIs(native.user32.DefWindowProcW.restype, ctypes.c_ssize_t)
        self.assertIs(native.kernel32.GetModuleHandleW.restype, ctypes.c_void_p)
        self.assertIs(native.gdi32.CreateFontW.restype, ctypes.c_void_p)
        self.assertEqual(len(native.gdi32.CreateFontW.argtypes), 14)

    def test_hit_testing_passes_clicks_through_and_activation_is_prevented(self):
        native = self.native()
        self.assertEqual(native._handle_message(123, taskbar.WM_NCHITTEST, 0, 0), taskbar.HTTRANSPARENT)
        self.assertEqual(native._handle_message(123, taskbar.WM_MOUSEACTIVATE, 0, 0), taskbar.MA_NOACTIVATE)
        native.user32.DefWindowProcW.assert_not_called()
        position = taskbar.WINDOWPOS()
        position.x, position.y = 500, 1008
        native._handle_message(123, taskbar.WM_WINDOWPOSCHANGING, 0, ctypes.addressof(position))
        self.assertEqual((position.x, position.y), (500, 1008))
        self.assertTrue(position.flags & taskbar.SWP_NOACTIVATE)

    def test_render_positions_only_own_window_without_activation_and_hides_on_none(self):
        native = self.native()
        placement = Placement(Rect(300, 1009, 420, 1039), dpi=96, scroll=False)
        native.render(placement)
        arguments = native.user32.SetWindowPos.call_args.args
        self.assertEqual(arguments[0], 123)
        self.assertEqual(arguments[1].value, ctypes.c_void_p(-1).value)
        self.assertEqual(arguments[2:6], (300, 1009, 120, 30))
        self.assertTrue(arguments[6] & taskbar.SWP_NOACTIVATE)
        native.user32.ShowWindow.assert_called_once_with(123, taskbar.SW_SHOWNOACTIVATE)
        native.render(None)
        self.assertEqual(native.user32.ShowWindow.call_args.args, (123, taskbar.SW_HIDE))
        self.assertFalse(native._visible)
        self.assertIsNone(native._placement)
        native.render(None)
        self.assertEqual(native.user32.ShowWindow.call_count, 2)

    def test_title_and_measurement_preserve_unicode_with_utf16_length(self):
        native = self.native()
        text = "Answer 日本語 café 🧬" * 100
        native.set_title(text)
        native.user32.SetWindowTextW.assert_called_once_with(123, text)
        self.assertEqual(native._title, text)
        width, height = native.measure(text)
        count = len(text.encode("utf-16-le")) // 2
        self.assertEqual(native.gdi32.GetTextExtentPoint32W.call_args.args[2], count)
        self.assertEqual((width, height), (count * 8, 18))
        native.user32.ReleaseDC.assert_called_once_with(123, 321)

    def test_marquee_is_clipped_inside_free_gap_and_keeps_complete_text(self):
        native = self.native()
        text = "Long exact answer 日本語 🧬" * 40
        native.set_title(text)
        native.render(Placement(Rect(300, 1009, 400, 1039), dpi=96, scroll=True))
        native._scroll_started = time.monotonic() - 4
        native._paint(123)
        native.gdi32.IntersectClipRect.assert_called_once_with(321, 0, 0, 100, 30)
        native.user32.FillRect.assert_called_once()
        self.assertEqual(native._title, text)
        calls = native.gdi32.TextOutW.call_args_list
        self.assertTrue(calls)
        self.assertLess(calls[0].args[1], 0)
        for call in calls:
            self.assertEqual(call.args[3], text)
            self.assertEqual(call.args[4], len(text.encode("utf-16-le")) // 2)
        native.gdi32.RestoreDC.assert_called_once_with(321, 1)
        native.user32.EndPaint.assert_called_once()

    def test_marquee_repeats_across_large_elapsed_times_without_unbounded_offsets(self):
        for elapsed in (0, 0.5, 1, 10, 1000, 10**9):
            with self.subTest(elapsed=elapsed):
                positions = taskbar._marquee_positions(1000, 100, elapsed)
                self.assertEqual(len(positions), 2)
                self.assertGreaterEqual(positions[0], -1026)
                self.assertLessEqual(positions[0], 6)
                self.assertEqual(positions[1] - positions[0], 1032)
        self.assertEqual(taskbar._marquee_positions(20, 100, 1000), (6,))

    def test_fractional_dpi_font_rounding_scrolls_any_actual_overflow(self):
        native = self.native()
        native.set_title("A phrase that slightly exceeds its measured base-DPI width")
        # The layout's base-DPI estimate fitted, but actual hinted glyph widths
        # at this scale can be wider. Still show every character by scrolling.
        native.render(Placement(Rect(300, 1000, 400, 1038), dpi=120, scroll=False))
        self.assertTrue(native._scrolling)
        native.user32.InvalidateRect.reset_mock()
        native.render(Placement(Rect(300, 1000, 400, 1038), dpi=120, scroll=False))
        native.user32.InvalidateRect.assert_called_once_with(123, None, False)

    def test_dpi_changes_recreate_font_without_changing_stored_text(self):
        native = self.native()
        native.gdi32.CreateFontW.return_value = 890
        native.render(Placement(Rect(300, 1000, 600, 1060), dpi=192, scroll=False))
        self.assertEqual(native.gdi32.CreateFontW.call_args.args[0], -28)
        self.assertEqual(native._dpi, 192)
        self.assertEqual(native._font, 890)
        native.render(Placement(Rect(300, 1000, 450, 1030), dpi=96, scroll=False))
        native.gdi32.DeleteObject.assert_called_with(890)
        self.assertEqual(native._font, 789)
        self.assertEqual(native._title, "1,4,6")

    def test_update_and_timer_dispatch_and_callback_exceptions_never_escape_ctypes(self):
        native = self.native()
        self.assertEqual(native._window_proc(123, taskbar.WM_APP_UPDATE, 0, 0), 0)
        self.assertEqual(native._window_proc(123, taskbar.WM_TIMER, taskbar.TIMER_ID, 0), 0)
        self.assertEqual(native._on_update.call_count, 2)
        error = RuntimeError("render failed")
        native._on_update.side_effect = error
        self.assertEqual(native._window_proc(123, taskbar.WM_APP_UPDATE, 0, 0), 0)
        self.assertIs(native._callback_error, error)
        native.user32.DestroyWindow.assert_called_once_with(123)
        native.user32.PostQuitMessage.assert_called_once_with(1)

    def test_wake_is_asynchronous_and_destroy_kills_timer_and_posts_quit(self):
        native = self.native()
        native.wake()
        native.user32.PostMessageW.assert_called_once_with(123, taskbar.WM_APP_UPDATE, 0, 0)
        native._handle_message(123, taskbar.WM_CLOSE, 0, 0)
        native.user32.DestroyWindow.assert_called_once_with(123)
        native._handle_message(123, taskbar.WM_DESTROY, 0, 0)
        self.assertIsNone(native._hwnd)
        native.user32.KillTimer.assert_called_once_with(123, taskbar.TIMER_ID)
        native.user32.PostQuitMessage.assert_called_once_with(0)
        native.wake()
        self.assertEqual(native.user32.PostMessageW.call_count, 1)

    def test_cleanup_releases_fonts_brush_and_class_before_callback(self):
        native = self.native()
        callback = native._wndproc

        def unregister(*args):
            self.assertIs(native._wndproc, callback)
            return 1

        native.user32.UnregisterClassW.side_effect = unregister
        native._handle_message(123, taskbar.WM_DESTROY, 0, 0)
        native.close()
        native.user32.UnregisterClassW.assert_called_once_with(native._class_name, 456)
        self.assertEqual([call.args[0] for call in native.gdi32.DeleteObject.call_args_list], [789, 888])
        self.assertFalse(native._registered)
        self.assertIsNone(native._wndproc)
        native.close()
        self.assertEqual(native.user32.UnregisterClassW.call_count, 1)
        self.assertEqual(native.gdi32.DeleteObject.call_count, 2)


if __name__ == "__main__":
    unittest.main()
