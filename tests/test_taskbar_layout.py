"""Safety and DPI behavior for physical taskbar gap placement."""

from dataclasses import FrozenInstanceError, replace
import unittest

from question_volume.taskbar_layout import (
    Placement, Rect, TaskbarSnapshot, find_space,
)


class TaskbarLayoutTests(unittest.TestCase):
    def snapshot(self, bounds=Rect(0, 1000, 1920, 1048), occupied=(), **kwargs):
        return TaskbarSnapshot(bounds, tuple(occupied), observed_at=100, **kwargs)

    def place(self, snapshots, width=120, **kwargs):
        return find_space(snapshots, width, now=100, **kwargs)

    def assert_safe(self, placement, snapshot, margin=12):
        self.assertIsNotNone(placement)
        rect = placement.bounds
        self.assertEqual(rect.intersect(snapshot.bounds), rect)
        self.assertGreaterEqual(rect.left, snapshot.bounds.left + margin)
        self.assertLessEqual(rect.right, snapshot.bounds.right - margin)
        for occupied in snapshot.occupied:
            clipped = occupied.intersect(snapshot.bounds)
            if clipped is not None:
                protected = Rect(
                    clipped.left - margin, snapshot.bounds.top,
                    clipped.right + margin, snapshot.bounds.bottom,
                )
                self.assertIsNone(rect.intersect(protected))

    def test_rectangles_are_frozen_and_intersections_are_half_open(self):
        rect = Rect(-10, -5, 10, 15)
        self.assertEqual((rect.width, rect.height), (20, 20))
        self.assertEqual(rect.intersect(Rect(0, 0, 20, 20)), Rect(0, 0, 10, 15))
        self.assertIsNone(rect.intersect(Rect(10, -5, 30, 15)))
        self.assertIsNone(rect.intersect(Rect(0, 0, 0, 5)))
        with self.assertRaises(FrozenInstanceError):
            rect.left = 0

    def test_centers_short_text_inside_actual_taskbar_without_button(self):
        snapshot = self.snapshot()
        placement = self.place([snapshot])
        self.assertEqual(placement, Placement(Rect(894, 1009, 1026, 1039), 96, False))
        self.assert_safe(placement, snapshot)

    def test_avoids_search_weather_apps_and_tray(self):
        snapshot = self.snapshot(occupied=(
            Rect(0, 1000, 220, 1048),       # Weather / widgets.
            Rect(540, 1000, 750, 1048),     # Search and task view.
            Rect(750, 1000, 1260, 1048),    # Pinned / running applications.
            Rect(1620, 1000, 1920, 1048),   # Tray / quick settings / clock.
        ))
        placement = self.place([snapshot])
        self.assert_safe(placement, snapshot)
        self.assertGreaterEqual(placement.bounds.left, 1272)
        self.assertLessEqual(placement.bounds.right, 1608)

    def test_unordered_overlapping_controls_merge_into_one_protected_span(self):
        snapshot = self.snapshot(occupied=(
            Rect(1100, 1000, 1600, 1048), Rect(400, 1000, 900, 1048),
            Rect(800, 1000, 1200, 1048), Rect(1600, 1000, 1920, 1048),
        ))
        placement = self.place([snapshot])
        self.assert_safe(placement, snapshot)
        self.assertLessEqual(placement.bounds.right, 388)

    def test_partial_vertical_overlap_still_reserves_control_horizontal_span(self):
        snapshot = self.snapshot(occupied=(Rect(500, 990, 1700, 1001),))
        placement = self.place([snapshot])
        self.assert_safe(placement, snapshot)
        self.assertLessEqual(placement.bounds.right, 488)

    def test_clips_controls_to_taskbar_and_ignores_controls_outside_it(self):
        snapshot = self.snapshot(occupied=(
            Rect(-500, 1000, 800, 1048), Rect(1000, 1000, 2500, 1048),
            Rect(0, 0, 1920, 999),
        ))
        placement = self.place([snapshot], 1000)
        self.assertEqual(placement.bounds, Rect(812, 1009, 988, 1039))
        self.assertTrue(placement.scroll)
        self.assert_safe(placement, snapshot)

    def test_all_covered_and_narrow_icon_padding_produce_no_placement(self):
        for occupied in (
            (Rect(0, 1000, 1920, 1048),),
            (Rect(0, 1000, 900, 1048), Rect(970, 1000, 1920, 1048)),
        ):
            self.assertIsNone(self.place([self.snapshot(occupied=occupied)]))

    def test_requires_minimum_usable_width_plus_both_text_paddings(self):
        too_small = self.snapshot(Rect(0, 1000, 91, 1048))
        exact = self.snapshot(Rect(0, 1000, 92, 1048))
        self.assertIsNone(self.place([too_small], 1))
        self.assertEqual(self.place([exact], 1).bounds.width, 68)

    def test_long_text_uses_largest_gap_and_scrolls_within_it(self):
        snapshot = self.snapshot(occupied=(
            Rect(0, 1000, 100, 1048), Rect(500, 1000, 1300, 1048),
            Rect(1800, 1000, 1920, 1048),
        ))
        placement = self.place([snapshot], 2000)
        self.assertEqual(placement.bounds, Rect(1312, 1009, 1788, 1039))
        self.assertTrue(placement.scroll)
        self.assert_safe(placement, snapshot)

    def test_negative_monitor_coordinates_are_not_clamped_to_zero(self):
        snapshot = self.snapshot(Rect(-1920, -48, 0, 0), occupied=(
            Rect(-1920, -48, -1800, 0), Rect(-1200, -48, 0, 0),
        ))
        placement = self.place([snapshot])
        self.assert_safe(placement, snapshot)
        self.assertEqual(placement.bounds, Rect(-1566, -39, -1434, -9))

    def test_scales_text_minimum_width_padding_and_margins_for_monitor_dpi(self):
        snapshot = self.snapshot(Rect(0, 1000, 1920, 1096), dpi=192, occupied=(
            Rect(0, 1000, 1000, 1096), Rect(1600, 1000, 1920, 1096),
        ))
        placement = self.place([snapshot], 200)
        self.assertEqual(placement.dpi, 192)
        self.assertEqual(placement.bounds, Rect(1088, 1018, 1512, 1078))
        self.assert_safe(placement, snapshot, margin=24)

    def test_fractional_dpi_rounds_protected_spacing_outward(self):
        snapshot = self.snapshot(Rect(0, 1000, 1920, 1060), dpi=120, occupied=(
            Rect(0, 1000, 1000, 1060), Rect(1500, 1000, 1920, 1060),
        ))
        placement = self.place([snapshot], 1000, margin_dip=12.1)
        self.assertEqual(placement.bounds.left, 1016)
        self.assertEqual(placement.bounds.right, 1484)
        self.assertEqual(placement.bounds.height, 39)
        self.assert_safe(placement, snapshot, margin=16)

    def test_prefers_complete_text_on_lower_dpi_bar_over_larger_scrolling_gap(self):
        low = self.snapshot(Rect(0, 0, 600, 48))
        high = self.snapshot(Rect(0, 1000, 900, 1096), dpi=192)
        placement = self.place([high, low], 500)
        self.assertEqual(placement.dpi, 96)
        self.assertFalse(placement.scroll)
        self.assert_safe(placement, low)

    def test_snapshot_order_does_not_change_selection(self):
        first = self.snapshot(Rect(-1920, 1000, 0, 1048))
        second = self.snapshot()
        self.assertEqual(self.place([first, second]), self.place([second, first]))
        self.assertLess(self.place([first, second]).bounds.left, 0)

    def test_unknown_unreliable_stale_future_hidden_and_bad_dpi_fail_closed(self):
        base = self.snapshot()
        invalid = [
            None,
            object(),
            replace(base, reliable=False),
            replace(base, visible=False),
            replace(base, observed_at=98.99),
            replace(base, observed_at=100.01),
            replace(base, observed_at=float("nan")),
            replace(base, dpi=0),
            replace(base, dpi=-96),
            replace(base, dpi=96.5),
            replace(base, bounds=Rect(0, 1000, 0, 1048)),
            replace(base, bounds=Rect(0.0, 1000, 1920, 1048)),
            replace(base, occupied=(None,)),
            replace(base, occupied=(Rect(100, 1000, 99, 1048),)),
        ]
        for snapshot in invalid:
            with self.subTest(snapshot=snapshot):
                self.assertIsNone(self.place([snapshot]))
        self.assertIsNone(self.place([]))

    def test_one_invalid_snapshot_does_not_disable_another_verified_bar(self):
        valid = self.snapshot()
        self.assertEqual(
            self.place([replace(valid, reliable=False), valid]),
            self.place([valid]),
        )

    def test_maximum_snapshot_age_boundary_is_inclusive(self):
        snapshot = replace(self.snapshot(), observed_at=99)
        self.assertIsNotNone(self.place([snapshot]))

    def test_vertical_taskbars_and_auto_hidden_slivers_are_skipped(self):
        for bounds in (Rect(0, 0, 48, 1080), Rect(0, 1078, 1920, 1080)):
            self.assertIsNone(self.place([self.snapshot(bounds)]))

    def test_text_height_must_fit_entirely_inside_taskbar(self):
        snapshot = self.snapshot()
        self.assertIsNotNone(self.place([snapshot], content_height=36))
        self.assertIsNone(self.place([snapshot], content_height=37))

    def test_invalid_text_or_spacing_fails_closed(self):
        for width in (0, -1, float("nan"), float("inf"), True):
            self.assertIsNone(self.place([self.snapshot()], width))
        for kwargs in (
            {"content_height": 0}, {"margin_dip": -1},
            {"minimum_width_dip": 0}, {"padding_dip": -1},
            {"max_age": -1}, {"padding_dip": float("inf")},
        ):
            self.assertIsNone(self.place([self.snapshot()], **kwargs))


if __name__ == "__main__":
    unittest.main()
