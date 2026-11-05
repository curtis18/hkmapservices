"""Tests for SVG marker outlines. These do not start QGIS."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from svgdraw import marker_ring, rotate_marker


class MarkerOutlineTests(unittest.TestCase):
    def test_star_points_up(self) -> None:
        ring = marker_ring("star", 2)
        self.assertEqual(len(ring), 10)
        top = min(ring, key=lambda point: point[1])
        self.assertAlmostEqual(top[0], 0.0, places=5)
        self.assertLess(top[1], 0)

    def test_circle_stays_a_circle(self) -> None:
        self.assertEqual(marker_ring("circle", 2), [])
        self.assertEqual(marker_ring("heart", 2), [])

    def test_square_and_diamond_differ(self) -> None:
        self.assertEqual(len(marker_ring("square", 1)), 4)
        self.assertEqual(marker_ring("diamond", 1)[0], (0.0, -1.0))

    def test_clockwise_rotation_moves_the_top_to_the_right(self) -> None:
        turned = rotate_marker([(0.0, -1.0)], 90)
        self.assertAlmostEqual(turned[0][0], 1.0, places=5)
        self.assertAlmostEqual(turned[0][1], 0.0, places=5)


if __name__ == "__main__":
    unittest.main()
