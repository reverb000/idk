import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from question_volume.app import process_capture
from question_volume.capture import capture_region
from question_volume.config import Settings
from question_volume.models import Choice, Question
from question_volume.runtime import ClickGate
from question_volume.volume import answer_steps, volume_target


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


class VolumeTests(unittest.TestCase):
    def test_choice_to_exact_step_mapping(self):
        for label, expected in (("A", 1), ("B", 2), ("C", 3), ("D", 4), ("Z", 26)):
            self.assertEqual(answer_steps(label), expected)
            target, next_sign = volume_target(0.5, expected)
            self.assertAlmostEqual(target - 0.5, expected / 100)
            self.assertEqual(next_sign, -1)

    def test_auto_reverses_without_clipping(self):
        target, next_sign = volume_target(0.99, 4, next_sign=1)
        self.assertAlmostEqual(target, 0.95)
        self.assertEqual(next_sign, 1)
        self.assertEqual(volume_target(0, 4, next_sign=-1), (0.04, -1))

    def test_fixed_direction_refuses_partial_signal(self):
        self.assertEqual(volume_target(0.99, 4, direction="up"), (None, 1))
        self.assertEqual(volume_target(0.01, 4, direction="down"), (None, 1))

    def test_invalid_labels_rejected(self):
        for label in ("", "d", "DD", "1", None):
            with self.assertRaises(ValueError):
                answer_steps(label)


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
        self.solver.solve.return_value = "B"
        self.volume = Mock()
        self.volume.signal.return_value = 2
        self.stop = threading.Event()

    def run_pipeline(self):
        process_capture("image", (100, 100), self.reader, self.solver, self.volume, self.stop)

    def test_success_forwards_extracted_question_and_single_label(self):
        self.run_pipeline()
        self.solver.solve.assert_called_once_with(self.question)
        self.volume.signal.assert_called_once_with("B")

    def test_failed_ocr_does_not_send_api_request(self):
        self.reader.read.return_value = None
        self.run_pipeline()
        self.solver.solve.assert_not_called()
        self.volume.signal.assert_not_called()

    def test_abstention_does_not_change_volume(self):
        self.solver.solve.return_value = None
        self.run_pipeline()
        self.volume.signal.assert_not_called()

    def test_stop_while_api_in_flight_prevents_late_volume_change(self):
        def finish(question):
            self.stop.set()
            return "B"
        self.solver.solve.side_effect = finish
        self.run_pipeline()
        self.volume.signal.assert_not_called()

    def test_unknown_answer_does_not_signal(self):
        self.solver.solve.return_value = "D"
        with self.assertRaises(ValueError):
            self.run_pipeline()
        self.volume.signal.assert_not_called()

    def test_request_gate_blocks_overlapping_or_bouncing_clicks(self):
        gate = ClickGate(1)
        self.assertTrue(gate.claim(0))
        self.assertFalse(gate.claim(2))
        gate.release()
        self.assertFalse(gate.claim(0.5))
        self.assertTrue(gate.claim(2))


if __name__ == "__main__":
    unittest.main()
