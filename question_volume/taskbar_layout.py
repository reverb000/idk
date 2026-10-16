"""Find verified empty taskbar space without covering any taskbar controls.

All rectangles use physical screen pixels. Requested text dimensions and spacing
use 96-DPI device independent pixels. Missing, stale, or ambiguous observations
never produce a placement; the caller should hide its answer window in that case.
"""

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def intersect(self, other: "Rect") -> "Rect | None":
        if not _valid_rect(self) or not _valid_rect(other):
            return None
        result = Rect(
            max(self.left, other.left), max(self.top, other.top),
            min(self.right, other.right), min(self.bottom, other.bottom),
        )
        return result if _valid_rect(result) else None


@dataclass(frozen=True)
class TaskbarSnapshot:
    bounds: Rect
    occupied: tuple[Rect, ...]
    dpi: int = 96
    observed_at: float = 0
    reliable: bool = True
    visible: bool = True


@dataclass(frozen=True)
class Placement:
    bounds: Rect
    dpi: int
    scroll: bool


def _valid_rect(rect: object) -> bool:
    return (
        isinstance(rect, Rect)
        and all(type(value) is int for value in (
            rect.left, rect.top, rect.right, rect.bottom,
        ))
        and rect.right > rect.left
        and rect.bottom > rect.top
    )


def _finite(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def find_space(
    snapshots: Iterable[TaskbarSnapshot],
    content_width: float,
    content_height: float = 18,
    *,
    now: float,
    max_age: float = 1.0,
    margin_dip: float = 12,
    minimum_width_dip: float = 56,
    padding_dip: float = 6,
) -> Placement | None:
    """Return a placement entirely within fresh, verified horizontal free space.

    Every occupied control reserves its entire horizontal span plus a margin,
    even if it overlaps only part of the taskbar vertically. ``minimum_width``
    is usable text width, with padding added on both sides. Long answers scroll
    within a qualifying gap; they never spill across neighboring controls.
    """
    if not all(_finite(value) for value in (
        content_width, content_height, now, max_age, margin_dip,
        minimum_width_dip, padding_dip,
    )):
        return None
    if (
        content_width <= 0 or content_height <= 0 or max_age < 0
        or margin_dip < 0 or minimum_width_dip <= 0 or padding_dip < 0
    ):
        return None

    candidates = []
    for snapshot in snapshots:
        if not isinstance(snapshot, TaskbarSnapshot):
            continue
        bar = snapshot.bounds
        if (
            snapshot.reliable is not True or snapshot.visible is not True
            or not _valid_rect(bar) or bar.width <= bar.height
            or type(snapshot.dpi) is not int or snapshot.dpi <= 0
            or not _finite(snapshot.observed_at)
            or not 0 <= now - snapshot.observed_at <= max_age
            or not isinstance(snapshot.occupied, (tuple, list))
            or any(not _valid_rect(rect) for rect in snapshot.occupied)
        ):
            continue

        try:
            scale = snapshot.dpi / 96
            margin = math.ceil(margin_dip * scale)
            padding = math.ceil(padding_dip * scale)
            text_width = math.ceil(content_width * scale)
            height = math.ceil(content_height * scale) + 2 * padding
            minimum_width = math.ceil(minimum_width_dip * scale) + 2 * padding
        except (OverflowError, ValueError):
            continue
        if height > bar.height:
            # Auto-hidden taskbars often expose a one- or two-pixel strip.
            continue
        left, right = bar.left + margin, bar.right - margin
        if right - left < minimum_width:
            continue

        blocked = []
        for occupied in snapshot.occupied:
            intersection = bar.intersect(occupied)
            if intersection is not None:
                start = max(left, intersection.left - margin)
                end = min(right, intersection.right + margin)
                if end > start:
                    blocked.append((start, end))

        # Advancing a single cursor merges unordered and overlapping controls.
        gaps = []
        cursor = left
        for start, end in sorted(blocked):
            if start > cursor:
                gaps.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < right:
            gaps.append((cursor, right))

        desired_width = max(minimum_width, text_width + 2 * padding)
        for start, end in gaps:
            gap_width = end - start
            if gap_width < minimum_width:
                continue
            fits = desired_width <= gap_width
            width = desired_width if fits else gap_width
            x = start + (gap_width - width) // 2
            y = bar.top + (bar.height - height) // 2
            placement = Placement(
                Rect(x, y, x + width, y + height), snapshot.dpi, not fits,
            )
            # Prefer complete text, then the largest gap; coordinates provide
            # stable selection even if monitor enumeration order changes.
            key = (not fits, -gap_width, bar.left, bar.top, start, snapshot.dpi)
            candidates.append((key, placement))

    return min(candidates, key=lambda item: item[0])[1] if candidates else None
