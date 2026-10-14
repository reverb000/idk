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


if __name__ == "__main__":
    unittest.main()
