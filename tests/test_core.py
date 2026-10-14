import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from question_volume.app import process_capture
from question_volume.capture import capture_region
from question_volume.config import Settings
from question_volume.models import Answer, Choice, Question
from question_volume.output import AnswerOutput
from question_volume.runtime import ClickGate


class CaptureTests(unittest.TestCase):
    def test_negative_second_monitor_and_edge_clamping(self):
        monitors = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": -1280, "top": -200, "width": 1280, "height": 1024},
        ]
        self.assertEqual(capture_region(monitors, (-1250, -180), 1400, 1000),
                         {"left": -1280, "top": -200, "width": 1280, "height": 1000})
        self.assertEqual(capture_region(monitors, (1919, 1079), 1400, 1000),
                         {"left": 520, "top": 80, "width": 1400, "height": 1000})

    def test_gap_between_monitors_is_not_captured(self):
        with self.assertRaises(ValueError):
            capture_region([{"left": 0, "top": 0, "width": 800, "height": 600}], (-1, 0), 400, 400)


class ConfigurationTests(unittest.TestCase):
    def load(self, values):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(values), encoding="utf-8")
            return Settings.load(path)

    def test_defaults_and_overrides(self):
        self.assertEqual(self.load({}).step_percent, 1)
        self.assertEqual(self.load({"direction": "down", "step_percent": 2}).step_percent, 2)

    def test_invalid_or_misspelled_settings_rejected(self):
        for values in ({"step_percent": 0}, {"capture_width": True}, {"api_timeout_seconds": float("nan")},
                       {"direction": "sideways"}, {"api_key": "must-not-be-in-config"}, {"language": ""}):
            with self.assertRaises(ValueError):
                self.load(values)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.question = Question("What is 2+2?", (Choice("A", "3"), Choice("B", "4")))
        self.reader = Mock()
        self.reader.read.return_value = self.question
        self.solver = Mock()
        self.solver.solve.return_value = Answer("single_choice", ("B",))
        self.display = Mock()
        self.stop = threading.Event()
        self.output = AnswerOutput(self.display, self.stop)
        self.token = self.output.begin()

    def run_pipeline(self):
        process_capture("image", (100, 100), self.reader, self.solver, self.output, self.token)

    def test_success_forwards_extracted_question_and_displays_choice_number(self):
        self.run_pipeline()
        self.reader.read.assert_called_once_with("image", (100, 100))
        self.solver.solve.assert_called_once_with(self.question)
        self.display.clear.assert_called_once_with("…")
        self.display.show.assert_called_once_with("2")

    def test_no_question_skips_model_and_clears_display(self):
        self.reader.read.return_value = None
        self.run_pipeline()
        self.solver.solve.assert_not_called()
        self.display.show.assert_not_called()
        self.display.clear.assert_called_with("?")

    def test_abstention_clears_display(self):
        self.solver.solve.return_value = None
        self.run_pipeline()
        self.display.show.assert_not_called()
        self.display.clear.assert_called_with("?")

    def test_stop_while_model_in_flight_prevents_late_answer(self):
        def finish(question):
            self.stop.set()
            return Answer("single_choice", ("B",))
        self.solver.solve.side_effect = finish
        self.run_pipeline()
        self.display.show.assert_not_called()

    def test_multi_select_displays_comma_separated_choice_numbers(self):
        self.reader.read.return_value = Question("Select all correct statements.", tuple(Choice(label, label) for label in "ABCDEF"))
        self.solver.solve.return_value = Answer("multi_select", ("F", "D", "A"))
        self.run_pipeline()
        self.display.show.assert_called_once_with("1,4,6")

    def test_fill_blank_displays_exact_answer_text(self):
        self.reader.read.return_value = Question("The chemical symbol for gold is ____.", (), "fill_blank")
        self.solver.solve.return_value = Answer("fill_blank", text="Au")
        self.run_pipeline()
        self.display.show.assert_called_once_with("Au")

    def test_invalid_answer_never_reaches_display(self):
        for answer in (Answer("single_choice", ("D",)), Answer("single_choice", ("A", "B")),
                       Answer("multi_select", ("A", "A")), Answer("fill_blank", text="private answer")):
            with self.subTest(answer=answer):
                self.solver.solve.return_value = answer
                with self.assertRaises(ValueError):
                    self.run_pipeline()
                self.display.show.assert_not_called()

    def test_stop_before_capture_skips_ocr_and_model(self):
        self.stop.set()
        self.run_pipeline()
        self.reader.read.assert_not_called()
        self.solver.solve.assert_not_called()
        self.display.show.assert_not_called()

    def test_new_click_during_ocr_prevents_old_model_request(self):
        def read(image, cursor):
            self.output.begin()
            return self.question
        self.reader.read.side_effect = read
        self.run_pipeline()
        self.solver.solve.assert_not_called()
        self.display.show.assert_not_called()

    def test_new_click_during_model_request_prevents_old_answer(self):
        def finish(question):
            self.output.begin()
            return Answer("single_choice", ("B",))
        self.solver.solve.side_effect = finish
        self.run_pipeline()
        self.display.show.assert_not_called()
        self.assertEqual(self.display.clear.call_count, 2)

    def test_stale_capture_skips_ocr_and_model(self):
        self.output.begin()
        self.run_pipeline()
        self.reader.read.assert_not_called()
        self.solver.solve.assert_not_called()
        self.display.show.assert_not_called()

    def test_request_gate_blocks_overlapping_or_bouncing_clicks(self):
        gate = ClickGate(1)
        self.assertTrue(gate.claim(0))
        self.assertFalse(gate.claim(2))
        gate.release()
        self.assertFalse(gate.claim(0.5))
        self.assertTrue(gate.claim(2))


class AnswerOutputTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "answer.txt"
        self.display = Mock()
        self.stop = threading.Event()
        self.output = AnswerOutput(self.display, self.stop, self.path)

    def test_new_click_clears_visible_answer_and_invalidates_previous_token(self):
        first = self.output.begin()
        self.assertTrue(self.output.publish(first, "1,4,6"))
        second = self.output.begin()
        self.assertFalse(self.output.current(first))
        self.assertTrue(self.output.current(second))
        self.display.clear.assert_called_with("…")
        self.assertFalse(self.output.publish(first, "old answer"))
        self.output.clear_saved(second)
        self.assertFalse(self.path.exists())
        self.display.show.assert_called_once_with("1,4,6")

    def test_full_fill_answer_is_saved_without_truncation(self):
        token = self.output.begin()
        text = "A long answer including symbols: α, β, and γ. " * 20
        self.assertTrue(self.output.publish(token, text))
        self.assertEqual(self.path.read_text(encoding="utf-8"), text)
        self.display.show.assert_called_once_with(text)

    def test_current_failure_clears_display_and_saved_answer(self):
        token = self.output.begin()
        self.output.publish(token, "2")
        self.output.fail(token)
        self.display.clear.assert_called_with("?")
        self.assertFalse(self.path.exists())

    def test_stale_failure_cannot_erase_newer_answer(self):
        old = self.output.begin()
        latest = self.output.begin()
        self.output.publish(latest, "new answer")
        self.display.reset_mock()
        self.output.fail(old)
        self.display.clear.assert_not_called()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "new answer")

    def test_stop_prevents_publication_or_replacing_current_display(self):
        token = self.output.begin()
        self.stop.set()
        self.display.reset_mock()
        self.assertFalse(self.output.current(token))
        self.assertFalse(self.output.publish(token, "late answer"))
        self.output.fail(token)
        self.display.show.assert_not_called()
        self.display.clear.assert_not_called()
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
