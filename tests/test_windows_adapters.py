"""Exercise Windows mouse hooks without accessing native desktop input."""

from contextlib import ExitStack
from pathlib import Path
import queue
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from question_volume import app
from question_volume.config import Settings
from question_volume.models import Choice, Question
from question_volume.output import AnswerOutput
from question_volume.solver import SolverError


class MouseHookAdapterTests(unittest.TestCase):
    def run_listener(self, suppress_right_click, second_click=False, queue_size=5, unexpected_stop=False):
        click_queue, capture_queue = queue.Queue(queue_size), queue.Queue(5)
        right, left = object(), object()
        suppressed = []
        forwarded = []
        display = Mock()
        native_listener = Mock()
        output_instances = []

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
                    if second_click:
                        self.on_click(124, 457, right, True)
                    if not unexpected_stop:
                        (state / "stop.request").touch()

                def is_alive(self):
                    return not unexpected_stop

                def stop(self):
                    native_listener.stop()

                def join(self, timeout):
                    native_listener.join(timeout=timeout)

            def make_output(*args):
                output = AnswerOutput(*args)
                output_instances.append(output)
                return output

            pynput = ModuleType("pynput")
            pynput.mouse = SimpleNamespace(
                Button=SimpleNamespace(right=right, left=left), Listener=FakeListener,
            )
            stack.enter_context(patch.dict("sys.modules", {"pynput": pynput}))
            stack.enter_context(patch.object(app.queue, "Queue", side_effect=[click_queue, capture_queue]))
            threads = stack.enter_context(patch.object(app.threading, "Thread"))
            stack.enter_context(patch.object(app.threading, "Event", FakeEvent))
            stack.enter_context(patch.object(app, "AnswerOutput", side_effect=make_output))
            screenshot = stack.enter_context(patch.object(app, "screenshot_near_cursor"))
            solver = Mock()
            if unexpected_stop:
                with self.assertRaisesRegex(RuntimeError, "listener stopped unexpectedly"):
                    app.listen(Settings(suppress_right_click=suppress_right_click), Mock(), solver, display, state)
            else:
                app.listen(Settings(suppress_right_click=suppress_right_click), Mock(), solver, display, state)
            screenshot.assert_not_called()
            solver.solve.assert_not_called()
            native_listener.stop.assert_called_once_with()
            native_listener.join.assert_called_once_with(timeout=2)
            self.assertEqual(threads.return_value.join.call_count, 2)
            self.assertTrue(output_instances[0].stop.is_set())
            self.assertFalse((state / "stop.request").exists())
        return click_queue, capture_queue, suppressed, forwarded, display, output_instances[0]

    def test_hook_only_queues_and_suppresses_right_button_events(self):
        clicks, captures, suppressed, forwarded, display, _ = self.run_listener(True)
        self.assertEqual(clicks.get_nowait(), (1, (123, 456)))
        self.assertTrue(clicks.empty())
        self.assertTrue(captures.empty())
        self.assertEqual(suppressed, [0x0204, 0x0205, 0x0206])
        self.assertEqual(forwarded, [0x0201, 0x020A])
        display.clear.assert_called_once_with("…")
        display.show.assert_not_called()

    def test_opt_out_preserves_native_mouse_events_without_duplicate_questions(self):
        clicks, _, suppressed, forwarded, display, _ = self.run_listener(False)
        self.assertEqual(clicks.get_nowait(), (1, (123, 456)))
        self.assertTrue(clicks.empty())
        self.assertEqual(suppressed, [])
        self.assertEqual(forwarded, [0x0204, 0x0205, 0x0206, 0x0201, 0x020A])
        display.clear.assert_called_once_with("…")

    def test_each_new_click_invalidates_the_previous_queued_capture(self):
        clicks, captures, _, _, display, output = self.run_listener(True, second_click=True)
        self.assertEqual(clicks.get_nowait(), (1, (123, 456)))
        self.assertEqual(clicks.get_nowait(), (2, (124, 457)))
        self.assertEqual(output.generation, 2)
        self.assertTrue(captures.empty())
        self.assertEqual(display.clear.call_count, 2)
        display.show.assert_not_called()

    def test_queue_overflow_clears_new_answer_and_invalidates_old_work(self):
        clicks, captures, _, _, display, output = self.run_listener(True, second_click=True, queue_size=1)
        self.assertEqual(clicks.get_nowait(), (1, (123, 456)))
        self.assertTrue(clicks.empty())
        self.assertTrue(captures.empty())
        self.assertEqual(output.generation, 2)
        display.clear.assert_called_with("?")
        display.show.assert_not_called()

    def test_unexpected_native_listener_failure_still_stops_and_joins_workers(self):
        self.run_listener(True, unexpected_stop=True)


class WorkerFailureTests(unittest.TestCase):
    def test_worker_failure_clears_result_and_logs_only_safe_error_details(self):
        for error in (ValueError("private screen text"), SolverError("The local request failed.")):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                failed = threading.Event()
                reader, solver, display = Mock(), Mock(), Mock()
                reader.read.return_value = Question("What is 2 + 2?", (Choice("A", "3"), Choice("B", "4")))
                solver.solve.side_effect = error
                right = object()
                native_listener = Mock()
                deadline = time.monotonic() + 3

                def clear(status):
                    if status == "?":
                        failed.set()
                        (state / "stop.request").touch()

                display.clear.side_effect = clear

                class FakeListener:
                    def __init__(self, on_click, win32_event_filter):
                        self.on_click = on_click

                    def start(self):
                        self.on_click(100, 100, right, True)

                    def is_alive(self):
                        return time.monotonic() < deadline

                    def stop(self):
                        native_listener.stop()

                    def join(self, timeout):
                        native_listener.join(timeout=timeout)

                pynput = ModuleType("pynput")
                pynput.mouse = SimpleNamespace(Button=SimpleNamespace(right=right), Listener=FakeListener)
                with patch.dict("sys.modules", {"pynput": pynput}), \
                     patch.object(app, "screenshot_near_cursor", return_value=("image", (100, 100))), \
                     self.assertLogs("question_volume", level="ERROR") as logs:
                    app.listen(Settings(), reader, solver, display, state)
                self.assertTrue(failed.is_set())
                self.assertNotIn("private", "\n".join(logs.output))
                if isinstance(error, SolverError):
                    self.assertIn("The local request failed.", "\n".join(logs.output))
                else:
                    self.assertIn("ValueError", "\n".join(logs.output))
                display.clear.assert_called_with("?")
                display.show.assert_not_called()
                native_listener.stop.assert_called_once_with()
                native_listener.join.assert_called_once_with(timeout=2)
                self.assertFalse((state / "stop.request").exists())


if __name__ == "__main__":
    unittest.main()
