"""Exercise Windows adapters without accessing this machine's audio or input."""

from contextlib import ExitStack
from pathlib import Path
import queue
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from question_volume import app
from question_volume.config import Settings
from question_volume.volume import WindowsVolume


class FakeEndpoint:
    def __init__(self, level, quantize_first_write=False):
        self.level = level
        self.writes = []
        self.quantize_first_write = quantize_first_write

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, level, context):
        self.writes.append((level, context))
        self.level = level
        if self.quantize_first_write and len(self.writes) == 1:
            self.level += 0.01


class WindowsVolumeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.com = ModuleType("comtypes")
        self.com.CoInitialize = Mock()
        self.com.CoUninitialize = Mock()
        self.audio = SimpleNamespace(GetSpeakers=Mock())
        pycaw = ModuleType("pycaw")
        pycaw.__path__ = []
        pycaw_api = ModuleType("pycaw.pycaw")
        pycaw_api.AudioUtilities = self.audio
        self.stack.enter_context(patch.dict("sys.modules", {
            "comtypes": self.com, "pycaw": pycaw, "pycaw.pycaw": pycaw_api,
        }))
        self.stack.enter_context(patch("question_volume.volume.sys.platform", "win32"))

    def use_endpoint(self, endpoint):
        self.audio.GetSpeakers.return_value = SimpleNamespace(EndpointVolume=endpoint)

    def test_a_and_d_change_one_and_four_percentage_points(self):
        endpoint = FakeEndpoint(0.5)
        self.use_endpoint(endpoint)
        volume = WindowsVolume()
        self.assertAlmostEqual(volume.signal("A"), 1)
        self.assertAlmostEqual(endpoint.level, 0.51)
        self.assertAlmostEqual(volume.signal("D"), -4)
        self.assertAlmostEqual(endpoint.level, 0.47)
        self.assertEqual(self.com.CoInitialize.call_count, 2)
        self.assertEqual(self.com.CoUninitialize.call_count, 2)
        self.assertTrue(all(context is None for _, context in endpoint.writes))

    def test_current_default_endpoint_is_resolved_for_each_answer(self):
        old_output, new_output = FakeEndpoint(0.5), FakeEndpoint(0.9)
        self.audio.GetSpeakers.side_effect = [
            SimpleNamespace(EndpointVolume=old_output),
            SimpleNamespace(EndpointVolume=new_output),
        ]
        volume = WindowsVolume()
        volume.signal("A")
        self.assertAlmostEqual(volume.signal("D"), -4)
        self.assertAlmostEqual(old_output.level, 0.51)
        self.assertAlmostEqual(new_output.level, 0.86)
        self.assertEqual(self.audio.GetSpeakers.call_count, 2)

    def test_boundary_reversal_keeps_the_full_choice_number(self):
        endpoint = FakeEndpoint(0.99)
        self.use_endpoint(endpoint)
        volume = WindowsVolume()
        self.assertAlmostEqual(volume.signal("D"), -4)
        self.assertAlmostEqual(endpoint.level, 0.95)
        endpoint.level = 0.01
        self.assertAlmostEqual(volume.signal("D"), 4)
        self.assertAlmostEqual(endpoint.level, 0.05)

    def test_fixed_direction_does_not_emit_a_clipped_signal(self):
        endpoint = FakeEndpoint(0.99)
        self.use_endpoint(endpoint)
        self.assertIsNone(WindowsVolume(direction="up").signal("D"))
        self.assertEqual(endpoint.writes, [])
        self.com.CoInitialize.assert_called_once_with()
        self.com.CoUninitialize.assert_called_once_with()

    def test_quantized_hardware_is_restored_and_direction_is_not_advanced(self):
        endpoint = FakeEndpoint(0.5, quantize_first_write=True)
        self.use_endpoint(endpoint)
        volume = WindowsVolume()
        with self.assertRaisesRegex(RuntimeError, "cannot represent"):
            volume.signal("D")
        self.assertAlmostEqual(endpoint.writes[0][0], 0.54)
        self.assertAlmostEqual(endpoint.writes[1][0], 0.5)
        self.assertAlmostEqual(endpoint.level, 0.5)
        self.assertEqual(volume.next_sign, 1)
        self.com.CoInitialize.assert_called_once_with()
        self.com.CoUninitialize.assert_called_once_with()

    def test_audio_failure_still_balances_com_initialization(self):
        self.audio.GetSpeakers.side_effect = OSError("No default output")
        with self.assertRaises(OSError):
            WindowsVolume().signal("A")
        self.com.CoInitialize.assert_called_once_with()
        self.com.CoUninitialize.assert_called_once_with()

    def test_native_objects_are_released_before_com_uninitialization(self):
        events = []

        class TransientEndpoint(FakeEndpoint):
            def __del__(self):
                events.append("endpoint-release")

        class TransientDevice:
            def __init__(self):
                self.EndpointVolume = TransientEndpoint(0.5)

            def __del__(self):
                events.append("device-release")

        self.audio.GetSpeakers.side_effect = TransientDevice
        self.com.CoInitialize.side_effect = lambda: events.append("initialize")
        self.com.CoUninitialize.side_effect = lambda: events.append("uninitialize")
        WindowsVolume().signal("A")
        self.assertEqual(events[0], "initialize")
        self.assertEqual(events[-1], "uninitialize")
        self.assertIn("endpoint-release", events[1:-1])
        self.assertIn("device-release", events[1:-1])


class MouseHookAdapterTests(unittest.TestCase):
    def run_listener(self, suppress_right_click):
        click_queue, capture_queue = queue.Queue(5), queue.Queue(5)
        right, left = object(), object()
        suppressed = []
        forwarded = []

        class Suppressed(Exception):
            pass

        class FakeEvent:
            def __init__(self):
                self.stopped = False

            def is_set(self):
                return self.stopped

            def set(self):
                self.stopped = True

            def wait(self, timeout):
                return self.stopped

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            state = Path(directory)

            class FakeListener:
                def __init__(self, on_click, win32_event_filter):
                    self.on_click = on_click
                    self.filter = win32_event_filter
                    self.message = None

                def suppress_event(self):
                    suppressed.append(self.message)
                    raise Suppressed()

                def start(self):
                    position = SimpleNamespace(pt=SimpleNamespace(x=123, y=456))
                    for message, button, pressed in (
                        (0x0204, right, True),
                        (0x0205, right, False),
                        (0x0206, right, False),
                        (0x0201, left, True),
                        (0x020A, None, False),
                    ):
                        self.message = message
                        try:
                            if self.filter(message, position):
                                forwarded.append(message)
                                if button is not None:
                                    self.on_click(123, 456, button, pressed)
                        except Suppressed:
                            pass
                    (state / "stop.request").touch()

                def is_alive(self):
                    return True

                def stop(self):
                    pass

                def join(self, timeout):
                    pass

            pynput = ModuleType("pynput")
            pynput.mouse = SimpleNamespace(
                Button=SimpleNamespace(right=right, left=left), Listener=FakeListener,
            )
            stack.enter_context(patch.dict("sys.modules", {"pynput": pynput}))
            stack.enter_context(patch.object(app.queue, "Queue", side_effect=[click_queue, capture_queue]))
            stack.enter_context(patch.object(app.threading, "Thread"))
            stack.enter_context(patch.object(app.threading, "Event", FakeEvent))
            screenshot = stack.enter_context(patch.object(app, "screenshot_near_cursor"))
            solver = Mock()
            app.listen(Settings(suppress_right_click=suppress_right_click), Mock(), solver, Mock(), state)
            screenshot.assert_not_called()
            solver.solve.assert_not_called()
        return click_queue, capture_queue, suppressed, forwarded

    def test_hook_only_queues_and_suppresses_right_button_events(self):
        clicks, captures, suppressed, forwarded = self.run_listener(True)
        self.assertEqual(clicks.get_nowait(), (123, 456))
        self.assertTrue(clicks.empty())
        self.assertTrue(captures.empty())
        self.assertEqual(suppressed, [0x0204, 0x0205, 0x0206])
        self.assertEqual(forwarded, [0x0201, 0x020A])

    def test_opt_out_preserves_native_mouse_events_without_duplicate_questions(self):
        clicks, _, suppressed, forwarded = self.run_listener(False)
        self.assertEqual(clicks.get_nowait(), (123, 456))
        self.assertTrue(clicks.empty())
        self.assertEqual(suppressed, [])
        self.assertEqual(forwarded, [0x0204, 0x0205, 0x0206, 0x0201, 0x020A])


if __name__ == "__main__":
    unittest.main()
