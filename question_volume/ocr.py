"""Read one nearby choice or explicit fill-in-the-blank question from OCR."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

from .models import Choice, Question


_OPTION = re.compile(r"^\s*(?:\(\s*([A-Z])\s*\)|([A-Z])\s*[.):])\s*(.*)$", re.I)
_NUMBERED_QUESTION = re.compile(r"^\s*(?:Q(?:uestion)?\s*)?\d{1,4}\s*[.):]\s+", re.I)
_BLANK = re.compile(r"_{2,}|\[\s*blank\s*\]|<\s*blank\s*>", re.I)
_FILL_INSTRUCTION = re.compile(r"\b(?:fill[ -]in(?:[ -]the)?[ -]blanks?|fill[ -]the[ -]blanks?|short[ -]answer)\b", re.I)
_INPUT_MARKER = re.compile(r"^\s*(?:answer\s*:|\[\s*\]|_{2,})\s*$", re.I)


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
        return _distance(self.lines, cursor)


def _distance(lines: tuple[_Line, ...], cursor: tuple[int, int]) -> float:
    x, y = cursor
    left = min(line.left for line in lines)
    right = max(line.right for line in lines)
    top = min(line.top for line in lines)
    bottom = max(line.bottom for line in lines)
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


def _prompt_lines(lines: list[_Line], start: int, after: int = -1) -> list[_Line]:
    first = lines[start]
    text_slop = max(80, first.height * 5)
    maximum_gap = max(72, first.height * 5)
    prompt_lines: list[_Line] = []
    next_top = first.top
    for line in reversed(lines[:start]):
        if line.bottom <= after:
            continue
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


def _candidate(lines: list[_Line], start: int, fills: tuple[_Candidate, ...] = ()) -> _Candidate | None:
    first = lines[start]
    label_slop = max(40, first.height * 3)
    text_slop = max(80, first.height * 5)
    maximum_gap = max(72, first.height * 5)
    prior_fills = [max(line.bottom for line in fill.lines) for fill in fills
                   if max(line.bottom for line in fill.lines) < first.top
                   and abs(fill.lines[0].left - first.left) <= text_slop]
    prompt_lines = _prompt_lines(lines, start, max(prior_fills, default=-1))
    if not prompt_lines:
        return None

    # The next question need not have a numeric prefix. Its prompt still
    # belongs to its own A/B group, rather than the preceding final choice.
    next_question_top = math.inf
    for fill in fills:
        fill_top = min(line.top for line in fill.lines)
        if fill_top > first.top and abs(fill.lines[0].left - first.left) <= text_slop:
            next_question_top = min(next_question_top, fill_top)
    for index in range(start + 1, len(lines)):
        following = lines[index]
        label = _label(following)
        if label is not None and label[0] == "A" and abs(following.left - first.left) <= label_slop:
            following_prompt = _prompt_lines(lines, index)
            next_question_top = min(next_question_top, following_prompt[0].top if following_prompt else following.top)
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


def _fill_candidates(lines: list[_Line]) -> list[_Candidate]:
    """Recognize explicit response blanks, never infer them from missing options.

    Plain questions and UI labels are insufficient. A visible blank, explicit
    fill/short-answer instruction, or a question followed by an input marker
    is required. Adjacent lettered options make it a choice question instead.
    """
    candidates: list[_Candidate] = []
    for index, anchor in enumerate(lines):
        if _label(anchor) is not None:
            continue
        explicit = bool(_BLANK.search(anchor.text) or _FILL_INSTRUCTION.search(anchor.text))
        input_only = bool(_INPUT_MARKER.fullmatch(anchor.text))
        if not explicit and not input_only:
            continue
        slop = max(80, anchor.height * 5)
        gap = max(44, anchor.height * 3)
        used = [anchor]
        # A meaningful stem on the same line is already complete. Only collect
        # preceding context for a separate marker or an unnumbered wrapped stem.
        meaningful = re.sub(r"[^A-Za-z0-9]", "", _BLANK.sub("", anchor.text))
        instruction_text = _NUMBERED_QUESTION.sub("", anchor.text).rstrip(".: ")
        instruction_only = bool(_FILL_INSTRUCTION.fullmatch(instruction_text))
        has_wrapped_stem = input_only or len(re.findall(r"[A-Za-z0-9]+", _BLANK.sub("", anchor.text))) < 4
        if not _NUMBERED_QUESTION.match(anchor.text) and has_wrapped_stem:
            for previous in reversed(lines[:index]):
                if abs(previous.left - anchor.left) > slop or previous.top >= anchor.top:
                    continue
                if used[0].top - previous.bottom > gap or _label(previous) is not None:
                    break
                if _BLANK.search(previous.text) or _FILL_INSTRUCTION.search(previous.text):
                    break
                # Do not attach a preceding independent prompt to an already
                # complete sentence containing its own blank.
                if meaningful and not input_only and not instruction_only and "?" in previous.text:
                    break
                used.insert(0, previous)
                if _NUMBERED_QUESTION.match(previous.text) or "?" in previous.text or len(used) >= 6:
                    break
        # Instructions can precede a wrapped prompt and its input marker.
        for following in lines[index + 1:]:
            if abs(following.left - anchor.left) > slop or following.top <= anchor.top:
                continue
            if following.top - used[-1].bottom > gap:
                break
            if _label(following) is not None or _NUMBERED_QUESTION.match(following.text):
                break
            if _FILL_INSTRUCTION.search(following.text):
                break
            if _INPUT_MARKER.fullmatch(following.text):
                used.append(following)
                break
            if not instruction_only:
                break
            used.append(following)
            if _BLANK.search(following.text) or "?" in following.text or len(used) >= 6:
                break
        prompt = " ".join(line.text for line in used).strip()
        # Bare decorative lines or a generic "Answer:" label are not prompts.
        stem = _FILL_INSTRUCTION.sub("", _BLANK.sub("", prompt))
        stem = re.sub(r"\banswer\s*:", "", stem, flags=re.I)
        if len(re.findall(r"[A-Za-z0-9]+", stem)) < 2:
            continue
        if input_only and not explicit and "?" not in prompt:
            continue
        # Even a single A/C/etc. label nearby signals an incomplete MC layout.
        # Abstain rather than treating an unreadable option list as free text.
        last = used[-1]
        has_options = False
        for following in lines:
            if following.top < last.bottom or abs(following.left - anchor.left) > slop:
                continue
            if following.top - last.bottom > max(72, anchor.height * 5):
                continue
            if _NUMBERED_QUESTION.match(following.text):
                break
            if _label(following) is not None:
                has_options = True
                break
            # A separate question between the blank and labels owns them.
            if "?" in following.text or _BLANK.search(following.text) or _FILL_INSTRUCTION.search(following.text):
                break
        if has_options:
            continue
        # An instruction line and the visible blank can each anchor the same
        # response. Their overlapping geometry must not create false ambiguity.
        used_set = set(used)
        if any(used_set.issubset(set(existing.lines)) for existing in candidates):
            continue
        candidates = [existing for existing in candidates if not set(existing.lines).issubset(used_set)]
        candidates.append(_Candidate(Question(prompt=prompt, choices=(), kind="fill_blank"), tuple(used)))
    return candidates


def _extract_candidate(data: dict[str, Any], cursor: tuple[int, int], min_confidence: float) -> _Candidate | None:
    """Select the nearest coherent question; abstain when selection is ambiguous.

    Labels must start with A and proceed sequentially, with at least two
    choices. Missing/low-confidence word boxes and unclear layouts return None.
    """
    if not isinstance(data, dict):
        return None
    lines = _lines(data, min_confidence)
    fills = tuple(_fill_candidates(lines))
    candidates = list(fills)
    for index, line in enumerate(lines):
        label = _label(line)
        if label is not None and label[0] == "A":
            candidate = _candidate(lines, index, fills)
            if candidate is not None:
                candidates.append(candidate)
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: candidate.distance(cursor))
    # Visible but malformed option groups nearer the cursor must not be
    # replaced with a neighboring readable blank or choice question.
    understood = {line for candidate in candidates if candidate.question.kind == "choice" for line in candidate.lines}
    for index, line in enumerate(lines):
        if _label(line) is not None and line not in understood:
            region = tuple(_prompt_lines(lines, index) + [line])
            if _distance(region, cursor) + 8 < candidates[0].distance(cursor):
                return None
    # Do not substitute a distant readable question when the one beside the
    # cursor has missing labels or otherwise failed extraction.
    nearby_limit = max(100, min(line.height for line in candidates[0].lines) * 6)
    if candidates[0].distance(cursor) > nearby_limit:
        return None
    if len(candidates) > 1:
        ambiguity_margin = max(8, min(line.height for line in candidates[0].lines) / 2)
        if candidates[1].distance(cursor) - candidates[0].distance(cursor) <= ambiguity_margin:
            return None
    return candidates[0]


def extract_question(data: dict[str, Any], cursor: tuple[int, int], min_confidence: float = 40.0) -> Question | None:
    """Select a nearby coherent question, requiring explicit free-text cues."""
    candidate = _extract_candidate(data, cursor, min_confidence)
    return candidate.question if candidate is not None else None


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
        candidate = _extract_candidate(data, cursor, self.min_confidence)
        if candidate is not None and candidate.question.kind == "choice":
            return candidate.question
        # Sparse mode sometimes drops short standalone labels. One uniform
        # block pass with larger glyphs preserves them without relaxing the
        # confidence threshold. Avoid enlarging already large captures.
        scale = 2 if image.width * image.height <= 2_000_000 else 1
        fallback_image = image.resize((image.width * scale, image.height * scale)) if scale > 1 else image
        data = pytesseract.image_to_data(fallback_image, lang=self.language, config="--psm 6",
                                        output_type=pytesseract.Output.DICT, timeout=12)
        fallback_cursor = (cursor[0] * scale, cursor[1] * scale)
        fallback = _extract_candidate(data, fallback_cursor, self.min_confidence)
        if fallback is None:
            return candidate.question if candidate is not None else None
        if candidate is None or fallback.distance(fallback_cursor) / scale <= candidate.distance(cursor) + 8:
            return fallback.question
        return candidate.question
