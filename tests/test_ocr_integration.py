"""Exercise the installed Tesseract engine on an actual rendered screenshot."""

from pathlib import Path
import shutil
import unittest

from PIL import Image, ImageDraw, ImageFont

from question_volume.ocr import OCRReader


class ActualOCRTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("tesseract") is None:
            raise unittest.SkipTest("Tesseract executable is unavailable")
        font_paths = (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
        )
        font_path = next((path for path in font_paths if path.exists()), None)
        if font_path is None:
            raise unittest.SkipTest("DejaVu Sans or Arial font is unavailable")
        image = Image.new("RGB", (1100, 650), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(font_path), 28)
        for y, text in (
            (40, "1. What is 2 plus 2?"), (90, "A. 3"), (140, "B. 4"),
            (190, "C. 5"), (240, "D. 6"),
            (360, "2. Which planet is known as the Red Planet?"),
            (410, "A. Venus"), (460, "B. Earth"), (510, "C. Jupiter"), (560, "D. Mars"),
        ):
            draw.text((40, y), text, font=font, fill="black")
        cls.image = image
        cls.font = font

    def render(self, rows, size=(1100, 650)):
        image = Image.new("RGB", size, "white")
        draw = ImageDraw.Draw(image)
        for y, text in rows:
            draw.text((40, y), text, font=self.font, fill="black")
        return image

    def test_cursor_near_first_question(self):
        question = OCRReader().read(self.image, (220, 45))
        self.assertIsNotNone(question)
        self.assertIn("What is 2 plus 2?", question.prompt)
        self.assertEqual([choice.label for choice in question.choices], ["A", "B", "C", "D"])
        self.assertEqual([choice.text for choice in question.choices], ["3", "4", "5", "6"])

    def test_cursor_near_second_question(self):
        question = OCRReader().read(self.image, (250, 375))
        self.assertIsNotNone(question)
        self.assertIn("Which planet is known as the Red Planet?", question.prompt)
        self.assertEqual([choice.label for choice in question.choices], ["A", "B", "C", "D"])
        self.assertEqual([choice.text for choice in question.choices], ["Venus", "Earth", "Jupiter", "Mars"])

    def test_rendered_fill_blank_prompt(self):
        image = self.render([
            (40, "Fill in the blank."),
            (90, "The capital of France is _____."),
        ])
        question = OCRReader().read(image, (250, 95))
        self.assertIsNotNone(question)
        self.assertEqual(question.kind, "fill_blank")
        self.assertEqual(question.choices, ())
        self.assertIn("capital of France", question.prompt)

    def test_rendered_multiselect_preserves_instructions_and_six_choices(self):
        image = self.render([
            (40, "1. Which numbers are prime?"),
            (85, "Select all that apply."),
            (145, "A. 2"), (195, "B. 4"), (245, "C. 6"),
            (295, "D. 3"), (345, "E. 8"), (395, "F. 5"),
        ])
        question = OCRReader().read(image, (250, 45))
        self.assertIsNotNone(question)
        self.assertEqual(question.kind, "choice")
        self.assertIn("Select all that apply", question.prompt)
        self.assertEqual([choice.label for choice in question.choices], list("ABCDEF"))
        self.assertEqual([choice.text for choice in question.choices], ["2", "4", "6", "3", "8", "5"])


if __name__ == "__main__":
    unittest.main()
