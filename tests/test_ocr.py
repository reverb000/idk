"""Geometry and ambiguity checks for cursor-near OCR extraction."""

import unittest
from unittest.mock import MagicMock, patch

from question_volume.ocr import OCRReader, extract_question


def ocr_data(rows):
    data = {key: [] for key in ("text", "conf", "left", "top", "width", "height", "block_num", "par_num", "line_num")}
    for line_num, row in enumerate(rows, start=1):
        text, left, top = row[:3]
        confidence = row[3] if len(row) > 3 else 95
        for word in text.split():
            width = max(8, len(word) * 8)
            values = (word, confidence, left, top, width, 16, line_num, 1, 1)
            for key, value in zip(data, values):
                data[key].append(value)
            left += width + 8
    return data


class ExtractQuestionTests(unittest.TestCase):
    def test_wrapped_prompt_and_multiline_choices(self):
        data = ocr_data([
            ("1. Which planet is", 30, 20), ("closest to the Sun?", 30, 42),
            ("A. Mercury", 30, 82), ("B) Venus", 30, 112),
            ("(C) Earth with its", 30, 142), ("single moon", 58, 164),
            ("D. Mars", 30, 194),
        ])
        question = extract_question(data, (150, 40))
        self.assertIsNotNone(question)
        self.assertEqual(question.prompt, "1. Which planet is closest to the Sun?")
        self.assertEqual([choice.label for choice in question.choices], ["A", "B", "C", "D"])
        self.assertEqual(question.choices[2].text, "Earth with its single moon")

    def test_nearest_of_vertical_questions(self):
        data = ocr_data([
            ("1. What is two plus two?", 20, 20), ("A. Four", 20, 50), ("B. Five", 20, 80),
            ("2. What is three plus three?", 20, 150), ("A. Six", 20, 180), ("B. Seven", 20, 210),
        ])
        self.assertIn("three", extract_question(data, (70, 152)).prompt)
        self.assertEqual(extract_question(data, (50, 65)).choices[0].text, "Four")

    def test_nearest_of_side_by_side_questions(self):
        data = ocr_data([
            ("1. Pick a cat?", 20, 20), ("2. Pick a dog?", 420, 20),
            ("A. Lion", 20, 50), ("A. Wolf", 420, 50),
            ("B. Tiger", 20, 80), ("B. Fox", 420, 80),
        ])
        question = extract_question(data, (450, 25))
        self.assertEqual(question.choices[0].text, "Wolf")
        self.assertEqual(len(question.choices), 2)

    def test_next_unnumbered_question_is_not_an_option_continuation(self):
        data = ocr_data([
            ("What is two plus two?", 20, 20), ("A. Four", 20, 50), ("B. Five", 20, 80),
            ("What is three plus three?", 20, 120), ("A. Six", 20, 150), ("B. Seven", 20, 180),
        ])
        first = extract_question(data, (50, 65))
        second = extract_question(data, (50, 125))
        self.assertEqual(first.choices[1].text, "Five")
        self.assertEqual(second.choices[0].text, "Six")

    def test_equidistant_questions_abstain(self):
        data = ocr_data([
            ("1. Pick a cat?", 20, 20), ("A. Lion", 20, 50), ("B. Tiger", 20, 80),
            ("2. Pick a dog?", 20, 180), ("A. Wolf", 20, 210), ("B. Fox", 20, 240),
        ])
        self.assertIsNone(extract_question(data, (40, 138)))

    def test_gaps_missing_labels_and_prompt_abstain(self):
        for rows in (
            [("A. First", 20, 20), ("B. Second", 20, 50)],
            [("Pick a choice?", 20, 20), ("A. First", 20, 50)],
            [("Pick a choice?", 20, 20), ("A. First", 20, 50), ("C. Third", 20, 80)],
            [("Pick a choice?", 20, 20), ("A. First", 20, 50), ("B. Second", 20, 400)],
        ):
            with self.subTest(rows=rows):
                self.assertIsNone(extract_question(ocr_data(rows), (20, 20)))

    def test_choices_support_e_f_and_spaced_labels(self):
        data = ocr_data([("Select an answer?", 20, 20)] +
                        [(f"( {letter} ) Answer {letter}", 20, 50 + index * 30)
                         for index, letter in enumerate("ABCDEF")])
        question = extract_question(data, (20, 20))
        self.assertEqual(len(question.choices), 6)
        self.assertEqual(question.choices[-1].label, "F")

    def test_lowercase_ocr_labels_are_normalized(self):
        data = ocr_data([("Choose an answer?", 20, 20), ("a. First", 20, 50),
                         ("b. Second", 20, 80), ("c. Third", 20, 110)])
        self.assertEqual([choice.label for choice in extract_question(data, (20, 20)).choices], ["A", "B", "C"])

    def test_distant_question_does_not_replace_unreadable_nearby_question(self):
        data = ocr_data([("Unclear nearby question?", 20, 20), ("First unlabeled option", 20, 50),
                         ("2. Distant readable question?", 20, 350), ("A. First", 20, 380), ("B. Second", 20, 410)])
        self.assertIsNone(extract_question(data, (50, 25)))

    def test_low_confidence_or_invalid_data_abstain(self):
        self.assertIsNone(extract_question({}, (0, 0)))
        self.assertIsNone(extract_question(None, (0, 0)))
        data = ocr_data([("Choose an answer?", 20, 20, 20), ("A. First", 20, 50, 20), ("B. Second", 20, 80, 20)])
        self.assertIsNone(extract_question(data, (20, 20)))
        data["conf"] = []
        self.assertIsNone(extract_question(data, (20, 20)))
        data["conf"] = [float("nan")] * len(data["text"])
        self.assertIsNone(extract_question(data, (20, 20)))

    def test_geometry_fallback_without_layout_identifiers(self):
        data = ocr_data([("Choose an answer?", 20, 20), ("A. First", 20, 50), ("B. Second", 20, 80)])
        for key in ("block_num", "par_num", "line_num"):
            del data[key]
        self.assertEqual(extract_question(data, (20, 20)).choices[1].text, "Second")

    def test_split_label_and_text_blocks_join(self):
        data = ocr_data([("Choose an answer?", 20, 20), ("A.", 20, 50), ("First", 52, 50),
                         ("B.", 20, 80), ("Second", 52, 80)])
        self.assertEqual(extract_question(data, (20, 20)).choices[0].text, "First")

    def test_reader_uses_bounded_tesseract_call(self):
        tesseract = MagicMock()
        tesseract.Output.DICT = "dict"
        tesseract.image_to_data.return_value = ocr_data([
            ("Choose an answer?", 20, 20), ("A. First", 20, 50), ("B. Second", 20, 80)])
        image = object()
        with patch.dict("sys.modules", {"pytesseract": tesseract}):
            result = OCRReader(tesseract_cmd="test-tesseract", language="eng").read(image, (20, 20))
        self.assertIsNotNone(result)
        tesseract.image_to_data.assert_called_once_with(image, lang="eng", config="--psm 11", output_type="dict", timeout=12)
        self.assertEqual(tesseract.pytesseract.tesseract_cmd, "test-tesseract")

    def test_reader_falls_back_once_without_relaxing_confidence(self):
        tesseract = MagicMock()
        tesseract.Output.DICT = "dict"
        # The second OCR pass uses a 2x image, so these boxes are also doubled.
        good_data = ocr_data([("Choose an answer?", 40, 40), ("a. First", 40, 100), ("b. Second", 40, 160)])
        good_data["height"] = [32] * len(good_data["text"])
        tesseract.image_to_data.side_effect = [{}, good_data]
        image = MagicMock(width=500, height=300)
        fallback_image = image.resize.return_value
        with patch.dict("sys.modules", {"pytesseract": tesseract}):
            result = OCRReader().read(image, (30, 30))
        self.assertEqual(result.choices[0].text, "First")
        image.resize.assert_called_once_with((1000, 600))
        self.assertEqual(tesseract.image_to_data.call_count, 2)
        tesseract.image_to_data.assert_any_call(fallback_image, lang="eng", config="--psm 6", output_type="dict", timeout=12)

    def test_reader_stops_after_two_unreadable_passes(self):
        tesseract = MagicMock()
        tesseract.image_to_data.return_value = {}
        image = MagicMock(width=500, height=300)
        with patch.dict("sys.modules", {"pytesseract": tesseract}):
            self.assertIsNone(OCRReader().read(image, (30, 30)))
        self.assertEqual(tesseract.image_to_data.call_count, 2)


if __name__ == "__main__":
    unittest.main()
