"""Read one nearby, clearly labeled multiple-choice question from OCR output."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

from .models import Choice, Question


_OPTION = re.compile(r"^\s*(?:\(\s*([A-Z])\s*\)|([A-Z])\s*[.):])\s*(.*)$", re.I)
_NUMBERED_QUESTION = re.compile(r"^\s*(?:Q(?:uestion)?\s*)?\d{1,4}\s*[.):]\s+", re.I)


@dataclass(frozen=True)
class _Line:
    text: str
    left: int
    top: int
    right: int
    bottom: int

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)


@dataclass(frozen=True)
class _Candidate:
    question: Question
    lines: tuple[_Line, ...]

    def distance(self, cursor: tuple[int, int]) -> float:
        x, y = cursor
        left = min(line.left for line in self.lines)
        right = max(line.right for line in self.lines)
        top = min(line.top for line in self.lines)
        bottom = max(line.bottom for line in self.lines)
        return math.hypot(max(left - x, 0, x - right), max(top - y, 0, y - bottom))


def _item(data: dict[str, Any], key: str, index: int, default: Any = None) -> Any:
    values = data.get(key)
    if isinstance(values, (list, tuple)) and index < len(values):
        return values[index]
    return default


def _lines(data: dict[str, Any], min_confidence: float) -> list[_Line]:
    """Keep Tesseract's line boundaries; tolerate absent layout identifiers."""
    texts = data.get("text")
    if not isinstance(texts, (list, tuple)):
        return []
    groups: dict[tuple[int, int, int], list[tuple[str, int, int, int, int]]] = {}
    fallback: list[tuple[str, int, int, int, int]] = []
    for index, raw in enumerate(texts[:4000]):
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            confidence = float(_item(data, "conf", index))
            left = int(_item(data, "left", index))
            top = int(_item(data, "top", index))
            width = int(_item(data, "width", index))
            height = int(_item(data, "height", index))
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(confidence) or confidence < min_confidence or width <= 0 or height <= 0:
            continue
        word = (raw.strip(), left, top, left + width, top + height)
        try:
            key = tuple(int(_item(data, field, index)) for field in ("block_num", "par_num", "line_num"))
        except (TypeError, ValueError, OverflowError):
            fallback.append(word)
        else:
            groups.setdefault(key, []).append(word)

    # Layout identifiers normally exist. Geometry provides a modest fallback
    # for OCR adapters that supply only word boxes.
    if fallback:
        fallback_groups: list[list[tuple[str, int, int, int, int]]] = []
        for word in sorted(fallback, key=lambda item: (item[2], item[1])):
            for group in reversed(fallback_groups):
                first = group[0]
                if abs(word[2] - first[2]) <= max(3, (first[4] - first[2]) // 2):
                    if word[1] <= max(item[3] for item in group) + max(80, 5 * (word[4] - word[2])):
                        group.append(word)
                        break
            else:
                fallback_groups.append([word])
        for index, group in enumerate(fallback_groups):
            groups[(-1, -1, index)] = group

    lines = []
    for words in groups.values():
        # Malformed identifiers should never concatenate distant rows.
        rows: list[list[tuple[str, int, int, int, int]]] = []
        for word in sorted(words, key=lambda item: (item[2], item[1])):
            if rows and abs(word[2] - rows[-1][0][2]) <= max(3, (word[4] - word[2]) // 2):
                rows[-1].append(word)
            else:
                rows.append([word])
        for row in rows:
            ordered = sorted(row, key=lambda item: item[1])
            lines.append(_Line(" ".join(item[0] for item in ordered), min(item[1] for item in row),
                               min(item[2] for item in row), max(item[3] for item in row),
                               max(item[4] for item in row)))

    # PSM 11 can place a label and its text in separate blocks on the same row.
    merged: list[_Line] = []
    for line in sorted(lines, key=lambda item: (item.top, item.left)):
        if merged:
            previous = merged[-1]
            gap = line.left - previous.right
            same_row = abs(line.top - previous.top) <= max(3, min(line.height, previous.height) // 3)
            if same_row and 0 <= gap <= max(40, 2 * min(line.height, previous.height)):
                merged[-1] = _Line(previous.text + " " + line.text, previous.left,
                                   min(previous.top, line.top), line.right, max(previous.bottom, line.bottom))
                continue
        merged.append(line)
    return merged


def _label(line: _Line) -> tuple[str, str] | None:
    match = _OPTION.match(line.text)
    if match is None:
        return None
    return (match.group(1) or match.group(2)).upper(), match.group(3).strip()


def _prompt_lines(lines: list[_Line], start: int) -> list[_Line]:
    first = lines[start]
    text_slop = max(80, first.height * 5)
    maximum_gap = max(72, first.height * 5)
    prompt_lines: list[_Line] = []
    next_top = first.top
    for line in reversed(lines[:start]):
        if abs(line.left - first.left) > text_slop or line.right < first.left:
            continue
        if line.top >= first.top:
            continue
        if next_top - line.bottom > maximum_gap or _label(line) is not None:
            break
        prompt_lines.append(line)
        next_top = line.top
        if _NUMBERED_QUESTION.match(line.text) or len(prompt_lines) >= 10:
            break
    prompt_lines.reverse()
    return prompt_lines


def _candidate(lines: list[_Line], start: int) -> _Candidate | None:
    first = lines[start]
    label_slop = max(40, first.height * 3)
    text_slop = max(80, first.height * 5)
    maximum_gap = max(72, first.height * 5)
    prompt_lines = _prompt_lines(lines, start)
    if not prompt_lines:
        return None

    # The next question need not have a numeric prefix. Its prompt still
    # belongs to its own A/B group, rather than the preceding final choice.
    next_question_top = math.inf
    for index in range(start + 1, len(lines)):
        following = lines[index]
        label = _label(following)
        if label is not None and label[0] == "A" and abs(following.left - first.left) <= label_slop:
            following_prompt = _prompt_lines(lines, index)
            next_question_top = following_prompt[0].top if following_prompt else following.top
            break

    options: list[tuple[str, list[str]]] = []
    used_lines: list[_Line] = list(prompt_lines)
    last_bottom = first.top
    for line in lines[start:]:
        if line.top >= next_question_top:
            break
        label = _label(line)
        if abs(line.left - first.left) > (label_slop if label else text_slop):
            continue
        if line.top - last_bottom > maximum_gap:
            break
        if _NUMBERED_QUESTION.match(line.text):
            break
        if label is not None:
            letter, text = label
            expected = chr(ord("A") + len(options))
            if letter == "A" and options:
                break
            if letter != expected:
                return None
            options.append((letter, [text] if text else []))
        elif options:
            options[-1][1].append(line.text)
        else:
            return None
        used_lines.append(line)
        last_bottom = max(last_bottom, line.bottom)
    if len(options) < 2 or any(not parts for _, parts in options):
        return None
    prompt = " ".join(line.text for line in prompt_lines).strip()
    if len(prompt) < 3:
        return None
    question = Question(prompt=prompt, choices=tuple(Choice(label=label, text=" ".join(parts))
                                                     for label, parts in options))
    return _Candidate(question, tuple(used_lines))


def extract_question(data: dict[str, Any], cursor: tuple[int, int], min_confidence: float = 40.0) -> Question | None:
    """Select the nearest coherent question; abstain when selection is ambiguous.

    Labels must start with A and proceed sequentially, with at least two
    choices. Missing/low-confidence word boxes and unclear layouts return None.
    """
    if not isinstance(data, dict):
        return None
    lines = _lines(data, min_confidence)
    candidates = []
    for index, line in enumerate(lines):
        label = _label(line)
        if label is not None and label[0] == "A":
            candidate = _candidate(lines, index)
            if candidate is not None:
                candidates.append(candidate)
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: candidate.distance(cursor))
    # Do not substitute a distant readable question when the one beside the
    # cursor has missing labels or otherwise failed extraction.
    nearby_limit = max(100, min(line.height for line in candidates[0].lines) * 6)
    if candidates[0].distance(cursor) > nearby_limit:
        return None
    if len(candidates) > 1:
        ambiguity_margin = max(8, min(line.height for line in candidates[0].lines) / 2)
        if candidates[1].distance(cursor) - candidates[0].distance(cursor) <= ambiguity_margin:
            return None
    return candidates[0].question


class OCRReader:
    def __init__(self, tesseract_cmd: str | None = None, language: str = "eng", min_confidence: float = 40.0):
        self.tesseract_cmd = tesseract_cmd
        self.language = language
        self.min_confidence = min_confidence

    def read(self, image: Any, cursor: tuple[int, int]) -> Question | None:
        import pytesseract

        if self.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd
        data = pytesseract.image_to_data(image, lang=self.language, config="--psm 11",
                                        output_type=pytesseract.Output.DICT, timeout=12)
        question = extract_question(data, cursor, self.min_confidence)
        if question is not None:
            return question
        # Sparse mode sometimes drops short standalone labels. One uniform
        # block pass with larger glyphs preserves them without relaxing the
        # confidence threshold. Avoid enlarging already large captures.
        scale = 2 if image.width * image.height <= 2_000_000 else 1
        fallback_image = image.resize((image.width * scale, image.height * scale)) if scale > 1 else image
        data = pytesseract.image_to_data(fallback_image, lang=self.language, config="--psm 6",
                                        output_type=pytesseract.Output.DICT, timeout=12)
        return extract_question(data, (cursor[0] * scale, cursor[1] * scale), self.min_confidence)
