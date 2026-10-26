"""Tests for swipe clipping. Map layers are checked with QGIS."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import comparemath


class CompareMathTests(unittest.TestCase):
    def test_vertical_half(self) -> None:
        first, second, line = comparemath.clip_ranges(200, 100, "vertical", 50)
        self.assertEqual(first, (0, 0, 100, 100))
        self.assertEqual(second, (100, 0, 100, 100))
        self.assertEqual(line, (100, 0, 100, 100))

    def test_horizontal_half(self) -> None:
        first, second, line = comparemath.clip_ranges(200, 100, "horizontal", 50)
        self.assertEqual(first, (0, 0, 200, 50))
        self.assertEqual(second, (0, 50, 200, 50))
        self.assertEqual(line, (0, 50, 200, 50))

    def test_divider_hit_follows_the_line(self) -> None:
        self.assertTrue(comparemath.divider_hit(200, 100, "vertical", 50, 100, 40))
        self.assertFalse(comparemath.divider_hit(200, 100, "vertical", 50, 40, 40))
        self.assertTrue(comparemath.divider_hit(200, 100, "horizontal", 25, 10, 25))
        self.assertTrue(comparemath.divider_hit(200, 100, "horizontal", 25, 100, 37))
        self.assertFalse(comparemath.divider_hit(200, 100, "horizontal", 25, 10, 37))

    def test_position_stays_in_range(self) -> None:
        self.assertEqual(comparemath.clamp_position(-10), 0)
        self.assertEqual(comparemath.clamp_position(140), 100)
        self.assertEqual(comparemath.clamp_opacity(2), 1.0)
        self.assertEqual(comparemath.clamp_opacity(-1), 0.0)

    def test_side_opacity_leaves_the_other_side_opaque(self) -> None:
        self.assertEqual(comparemath.side_opacity(0.34, "right", "right"), 0.34)
        self.assertEqual(comparemath.side_opacity(0.34, "right", "left"), 1.0)
        self.assertEqual(comparemath.side_opacity(0.34, "left", "left"), 0.34)
        self.assertEqual(comparemath.side_opacity(0.34, "left", "right"), 1.0)
        self.assertEqual(comparemath.side_opacity(0.34, "both", "left"), 0.34)
        self.assertEqual(comparemath.side_opacity(0.34, "both", "right"), 0.34)
        with self.assertRaises(ValueError):
            comparemath.side_opacity(0.34, "both", "middle")

    def test_unknown_direction(self) -> None:
        with self.assertRaises(ValueError):
            comparemath.normalize_opacity_side("middle")
        with self.assertRaises(ValueError):
            comparemath.clip_ranges(10, 10, "diagonal", 50)


if __name__ == "__main__":
    unittest.main()
