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

    def test_explicit_fill_blank_has_no_choices(self):
        for text in ("1. The capital of France is ____.",
                     "1. The capital of France is [blank].",
                     "Short answer: What is the capital of France?"):
            with self.subTest(text=text):
                question = extract_question(ocr_data([(text, 20, 20)]), (35, 25))
                self.assertIsNotNone(question)
                self.assertEqual(question.kind, "fill_blank")
                self.assertEqual(question.choices, ())
                self.assertEqual(question.prompt, text)

    def test_fill_instruction_and_question_input_marker(self):
        for rows in (
            [("Fill in the blank.", 20, 20), ("The capital of France is ____.", 20, 50)],
            [("3. Fill in the blank.", 20, 20), ("The capital of France is", 20, 50)],
            [("What is the capital of France?", 20, 20), ("Answer:", 20, 50)],
            [("What is the capital of France?", 20, 20), ("[ ]", 20, 50)],
        ):
            with self.subTest(rows=rows):
                for cursor in ((50, 25), (50, 55)):
                    question = extract_question(ocr_data(rows), cursor)
                    self.assertIsNotNone(question)
                    self.assertEqual(question.kind, "fill_blank")
                    self.assertIn("capital of France", question.prompt)

    def test_nearest_of_two_fill_questions(self):
        data = ocr_data([("1. France has capital ____.", 20, 20),
                         ("2. Italy has capital ____.", 20, 180)])
        self.assertIn("Italy", extract_question(data, (50, 185)).prompt)

    def test_fill_prompt_excludes_unrelated_toolbar_text(self):
        data = ocr_data([("File Edit View Settings", 20, 20),
                         ("The capital of France is ____.", 20, 50)])
        self.assertEqual(extract_question(data, (50, 55)).prompt, "The capital of France is ____.")

    def test_wrapped_fill_stem_is_preserved(self):
        data = ocr_data([("1. The capital of", 20, 20), ("France is ____.", 20, 45)])
        self.assertEqual(extract_question(data, (50, 50)).prompt, "1. The capital of France is ____.")

    def test_mixed_fill_and_choice_questions_remain_separate(self):
        data = ocr_data([
            ("1. What is two plus two?", 20, 20), ("A. Four", 20, 50), ("B. Five", 20, 80),
            ("The capital of France is ____.", 20, 120),
            ("What is three plus three?", 20, 200), ("A. Six", 20, 230), ("B. Seven", 20, 260),
        ])
        first = extract_question(data, (50, 65))
        fill = extract_question(data, (50, 125))
        last = extract_question(data, (50, 205))
        self.assertEqual(first.choices[1].text, "Five")
        self.assertEqual(fill.kind, "fill_blank")
        self.assertEqual(fill.prompt, "The capital of France is ____.")
        self.assertEqual(last.prompt, "What is three plus three?")
        self.assertEqual(last.choices[0].text, "Six")

    def test_side_by_side_mixed_questions_choose_nearest(self):
        data = ocr_data([
            ("1. France has capital ____.", 20, 20), ("2. Pick a dog?", 420, 20),
            ("A. Wolf", 420, 50), ("B. Fox", 420, 80),
        ])
        self.assertEqual(extract_question(data, (40, 25)).kind, "fill_blank")
        self.assertEqual(extract_question(data, (450, 25)).choices[0].text, "Wolf")

    def test_blank_in_choice_stem_is_still_choice_question(self):
        data = ocr_data([("The capital of France is ____.", 20, 20),
                         ("A. Paris", 20, 50), ("B. Rome", 20, 80)])
        self.assertEqual(extract_question(data, (30, 25)).kind, "choice")

    def test_incomplete_or_malformed_choices_are_not_fill_blank(self):
        for options in (
            [("A. Paris", 20, 50)],
            [("A. Paris", 20, 50), ("C. Rome", 20, 80)],
            [("B. Rome", 20, 50), ("C. Berlin", 20, 80)],
        ):
            with self.subTest(options=options):
                data = ocr_data([("The capital of France is ____.", 20, 20)] + options)
                self.assertIsNone(extract_question(data, (30, 25)))

    def test_incomplete_nearby_choice_does_not_select_neighboring_fill(self):
        data = ocr_data([("What is two plus two?", 20, 20), ("A. Four", 20, 50),
                         ("2. France has capital ____.", 20, 120)])
        self.assertIsNone(extract_question(data, (40, 25)))
        self.assertEqual(extract_question(data, (40, 125)).kind, "fill_blank")

    def test_plain_questions_ui_text_and_decorative_blanks_abstain(self):
        for rows in (
            [("What is the capital of France?", 20, 20)],
            [("Search settings", 20, 20), ("Answer:", 20, 50)],
            [("____", 20, 20)],
            [("Fill in the blank", 20, 20)],
            [("Submit answer", 20, 20)],
        ):
            with self.subTest(rows=rows):
                self.assertIsNone(extract_question(ocr_data(rows), (30, 25)))

    def test_select_all_instructions_remain_in_choice_prompt(self):
        data = ocr_data([("1. Which numbers are prime?", 20, 20),
                         ("Select all that apply.", 20, 45),
                         ("A. 2", 20, 80), ("B. 4", 20, 110), ("C. 5", 20, 140)])
        question = extract_question(data, (30, 25))
        self.assertEqual(question.kind, "choice")
        self.assertIn("Select all that apply.", question.prompt)
        self.assertEqual([choice.label for choice in question.choices], ["A", "B", "C"])

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

    def test_reader_checks_fill_candidate_again_for_missing_nearby_choices(self):
        tesseract = MagicMock()
        tesseract.Output.DICT = "dict"
        sparse = ocr_data([("1. France has capital ____.", 20, 100)])
        complete = ocr_data([("2. What is two plus two?", 40, 40),
                             ("A. Four", 40, 100), ("B. Five", 40, 160),
                             ("1. France has capital ____.", 40, 280)])
        complete["height"] = [32] * len(complete["text"])
        tesseract.image_to_data.side_effect = [sparse, complete]
        image = MagicMock(width=500, height=300)
        with patch.dict("sys.modules", {"pytesseract": tesseract}):
            result = OCRReader().read(image, (30, 25))
        self.assertEqual(tesseract.image_to_data.call_count, 2)
        self.assertEqual(result.kind, "choice")
        self.assertEqual(result.choices[0].text, "Four")

    def test_reader_preserves_clear_fill_if_block_pass_loses_blank(self):
        tesseract = MagicMock()
        tesseract.Output.DICT = "dict"
        tesseract.image_to_data.side_effect = [ocr_data([("1. France has capital ____.", 20, 20)]), {}]
        image = MagicMock(width=500, height=300)
        with patch.dict("sys.modules", {"pytesseract": tesseract}):
            result = OCRReader().read(image, (30, 25))
        self.assertEqual(result.kind, "fill_blank")
        self.assertEqual(tesseract.image_to_data.call_count, 2)


if __name__ == "__main__":
    unittest.main()
