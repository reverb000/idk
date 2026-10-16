from pathlib import Path
import tempfile
import threading
import unittest
import string
from unittest.mock import Mock, patch

from question_volume.answers import answer_schema, parse_answer, taskbar_text
from question_volume.models import Answer, Choice, Question
from question_volume.output import AnswerOutput


class AnswerFormattingTests(unittest.TestCase):
    def setUp(self):
        self.question = Question("Select all correct choices.", tuple(Choice(chr(65+i), str(i)) for i in range(8)))

    def test_single_choice_indices_include_more_than_four(self):
        for label, expected in (("A", "1"), ("D", "4"), ("H", "8")):
            self.assertEqual(taskbar_text(Answer("single_choice", (label,)), self.question), expected)

    def test_multiselect_sorted_by_visible_choice_order(self):
        self.assertEqual(taskbar_text(Answer("multi_select", ("F", "A", "D")), self.question), "1,4,6")

    def test_every_letter_a_through_z_maps_to_its_number(self):
        question = Question("Which choice is correct?", tuple(Choice(label, label) for label in string.ascii_uppercase))
        for number, label in enumerate(string.ascii_uppercase, 1):
            with self.subTest(label=label):
                answer = parse_answer({"kind": "single_choice", "labels": [label], "text": None}, question)
                self.assertEqual(taskbar_text(answer, question), str(number))
        self.assertEqual(answer_schema(question)["properties"]["labels"]["items"]["enum"], list(string.ascii_uppercase))

    def test_multiselect_works_for_arbitrary_letters_and_all_letters(self):
        question = Question("Select all correct choices.", tuple(Choice(label, label) for label in string.ascii_uppercase))
        for labels, expected in ((("Z", "B", "E"), "2,5,26"), (("Y", "J", "M"), "10,13,25"),
                                 (("X", "A", "K", "Q"), "1,11,17,24")):
            with self.subTest(labels=labels):
                answer = parse_answer({"kind": "multi_select", "labels": list(labels), "text": None}, question)
                self.assertEqual(taskbar_text(answer, question), expected)
        all_labels = tuple(reversed(string.ascii_uppercase))
        answer = parse_answer({"kind": "multi_select", "labels": list(all_labels), "text": None}, question)
        self.assertEqual(taskbar_text(answer, question), "1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26")

    def test_fill_text_preserves_case_punctuation_and_unicode(self):
        question = Question("Fill in: ___", (), "fill_blank")
        for text in ("Paris", "H₂O", "12.50", "José de San Martín"):
            self.assertEqual(taskbar_text(Answer("fill_blank", text=text), question), text)

    def test_invalid_or_mismatched_answer_is_rejected(self):
        for answer in (Answer("single_choice", ("Z",)), Answer("single_choice", ("A", "D")),
                       Answer("multi_select", ("D", "D")), Answer("fill_blank", text="word")):
            with self.assertRaises(ValueError):
                taskbar_text(answer, self.question)

    def test_fill_answer_rejects_invisible_control_characters(self):
        question = Question("Fill in: ___", (), "fill_blank")
        for text in ("", "   ", "a\x00b", "a\nb", "a\x7fb", "a\x85b"):
            with self.assertRaises(ValueError):
                taskbar_text(Answer("fill_blank", text=text), question)

    def test_abstain_and_schema_shape(self):
        self.assertIsNone(parse_answer({"kind": "abstain", "labels": [], "text": None}, self.question))
        schema = answer_schema(self.question)
        self.assertEqual(set(schema["required"]), {"kind", "labels", "text"})
        self.assertFalse(schema["additionalProperties"])


class AnswerOutputTests(unittest.TestCase):
    def test_begin_does_not_touch_files_on_the_mouse_hook(self):
        display = Mock()
        output = AnswerOutput(display, threading.Event(), Path("/unused/answer.txt"))
        with patch.object(Path, "unlink", side_effect=AssertionError("No disk work on the mouse hook")):
            token = output.begin()
        self.assertTrue(output.current(token))
        display.clear.assert_called_once_with("…")

    def test_new_question_and_stop_reject_previous_results(self):
        display, stop = Mock(), threading.Event()
        output = AnswerOutput(display, stop)
        old = output.begin()
        current = output.begin()
        self.assertFalse(output.publish(old, "1"))
        output.fail(old)
        self.assertTrue(output.publish(current, "1,4,6"))
        display.show.assert_called_once_with("1,4,6")
        stop.set()
        self.assertFalse(output.publish(current, "wrong"))

    def test_full_unicode_answer_saved_and_cleared_on_current_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "answer.txt"
            output = AnswerOutput(Mock(), threading.Event(), path)
            token = output.begin()
            output.publish(token, "José de San Martín")
            self.assertEqual(path.read_text(encoding="utf-8"), "José de San Martín")
            output.fail(token)
            self.assertFalse(path.exists())

    def test_unwritable_answer_file_does_not_break_the_taskbar(self):
        display = Mock()
        output = AnswerOutput(display, threading.Event(), Path("/unused/answer.txt"))
        token = output.begin()
        with patch.object(Path, "write_text", side_effect=PermissionError), self.assertLogs("question_volume", "WARNING"):
            self.assertTrue(output.publish(token, "1"))
        with patch.object(Path, "unlink", side_effect=PermissionError), self.assertLogs("question_volume", "WARNING"):
            output.fail(token)
        display.show.assert_called_once_with("1")


if __name__ == "__main__":
    unittest.main()
