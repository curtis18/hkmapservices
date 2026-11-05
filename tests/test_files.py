"""Tests for database names and SVG drawing. QGIS writes the files."""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import filemath
import svgdraw


class FileMathTests(unittest.TestCase):
    def test_geometry_kinds(self) -> None:
        self.assertEqual(filemath.geometry_kind("Point"), "point")
        self.assertEqual(filemath.geometry_kind("MultiPointZ"), "point")
        self.assertEqual(filemath.geometry_kind("LineString"), "line")
        self.assertEqual(filemath.geometry_kind("MultiLineString"), "line")
        self.assertEqual(filemath.geometry_kind("MultiPolygon"), "polygon")
        self.assertIsNone(filemath.geometry_kind("No Geometry"))
        self.assertIsNone(filemath.geometry_kind("GeometryCollection"))
        self.assertIsNone(filemath.geometry_kind("CircularString"))

    def test_outputs(self) -> None:
        self.assertEqual(filemath.normalize_outputs(("fgdb", "gpkg", "gpkg")), ("fgdb", "gpkg"))
        with self.assertRaises(ValueError):
            filemath.normalize_outputs(())
        with self.assertRaises(ValueError):
            filemath.normalize_outputs(("svg",))

    def test_discover_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gdb = Path(directory) / "roads.gdb"
            gdb.mkdir()
            other = Path(directory) / "notes"
            other.mkdir()
            previous = Path(directory) / "hkmap-roads.previous-abc.gdb"
            previous.mkdir()
            gpkg = Path(directory) / "sites.gpkg"
            gpkg.write_bytes(b"")
            self.assertEqual(filemath.discover_sources(str(gdb)), [os.path.abspath(gdb)])
            self.assertEqual(filemath.discover_sources(str(gpkg)), [os.path.abspath(gpkg)])
            found = filemath.discover_sources(directory)
            self.assertEqual(found, [os.path.abspath(gdb)])
            self.assertEqual(filemath.source_stem(str(gdb)), "roads")
            self.assertTrue(filemath.is_working_name(previous.name))
            self.assertFalse(filemath.is_working_name("roads.gdb"))

    def test_measure_columns(self) -> None:
        self.assertEqual(
            filemath.measure_names("point", True, True),
            ("Northing", "Easting", "Latitude", "Longitude"),
        )
        self.assertEqual(filemath.measure_names("line", False, True), ("Length",))
        self.assertEqual(
            filemath.measure_names("polygon", True, True),
            ("Northing", "Easting", "Latitude", "Longitude", "Length", "Area"),
        )
        self.assertEqual(filemath.measure_names("table", True, True), ())
        added, targets = filemath.field_targets(
            ("NORTHING", "EASTING", "name"),
            ("Northing", "Easting", "Latitude", "Longitude"),
        )
        self.assertEqual(added, ("Latitude", "Longitude"))
        self.assertEqual(targets["Northing"], "NORTHING")
        self.assertEqual(targets["Easting"], "EASTING")
        self.assertEqual(filemath.round_places(836694.055, 2), 836694.06)
        self.assertEqual(filemath.round_places(400, 0), 400.0)
        self.assertTrue(filemath.is_geometry_table("No Geometry"))
        self.assertFalse(filemath.is_geometry_table("CircularString"))


class SvgDrawTests(unittest.TestCase):
    def test_sheet_follows_the_map(self) -> None:
        wide = svgdraw.sheet_for_view(800, 600)
        tall = svgdraw.sheet_for_view(600, 800)
        self.assertTrue(wide["landscape"])
        self.assertEqual((wide["page_width_mm"], wide["page_height_mm"]), (420.0, 297.0))
        self.assertFalse(tall["landscape"])
        self.assertEqual((tall["page_width_mm"], tall["page_height_mm"]), (297.0, 420.0))
        self.assertGreaterEqual(wide["offset_x_mm"], 0)
        self.assertLessEqual(wide["map_width_mm"], wide["page_width_mm"])
        self.assertLessEqual(wide["map_height_mm"], wide["page_height_mm"])

    def test_clip_keeps_coordinates_inside_the_view(self) -> None:
        rect = (0, 0, 10, 10)
        line = svgdraw.clip_polyline([(-5, 5), (15, 5)], rect)
        self.assertEqual(len(line), 1)
        for point in line[0]:
            self.assertTrue(svgdraw.point_inside(point, rect))
        ring = svgdraw.clip_polygon([(-5, -5), (15, -5), (15, 15), (-5, 15)], rect)
        self.assertGreaterEqual(len(ring), 4)
        for point in ring:
            self.assertTrue(svgdraw.point_inside(point, rect))
        self.assertFalse(svgdraw.point_inside((11, 5), rect))

    def test_svg_layer_group(self) -> None:
        sheet = svgdraw.sheet_for_view(800, 600)
        extent = (0, 0, 10, 10)
        clipped = svgdraw.clip_polygon([(-5, -5), (15, -5), (15, 15), (-5, 15)], extent)
        svg = svgdraw.build_svg(
            sheet,
            extent,
            [
                {
                    "name": "District",
                    "features": [
                        {
                            "kind": "polygon",
                            "rings": [clipped],
                            "fill": "#d7e6c8",
                            "stroke": "#1c4b3a",
                            "stroke_width": 0.4,
                        }
                    ],
                }
            ],
            b"\x89PNG\r\n",
            (100, 20, "100 m"),
        )
        self.assertIn('inkscape:groupmode="layer"', svg)
        self.assertIn('inkscape:label="District"', svg)
        self.assertIn('inkscape:label="Basemap"', svg)
        self.assertIn('inkscape:label="Scale bar"', svg)
        self.assertIn('inkscape:label="North arrow"', svg)
        self.assertIn("<image ", svg)
        self.assertIn('width="420mm"', svg)
        self.assertNotIn("15 15", svg)
        plain = svgdraw.build_svg(sheet, extent, [], None, None, False, False)
        self.assertNotIn('inkscape:label="Scale bar"', plain)
        self.assertNotIn('inkscape:label="North arrow"', plain)

    def test_svg_keeps_order_opacity_and_rotation(self) -> None:
        sheet = svgdraw.sheet_for_view(800, 600)
        extent = (0, 0, 10, 10)
        png = b"\x89PNG\r\n\x1a\n"
        svg = svgdraw.build_svg(
            sheet,
            extent,
            [
                {"name": "Bottom raster", "image": png},
                {
                    "name": "Top vector",
                    "features": [
                        {
                            "kind": "polygon",
                            "rings": [[(1, 1), (9, 1), (9, 9), (1, 9)]],
                            "fill": "#000000",
                            "fill_opacity": 0.5,
                            "stroke": "#1c4b3a",
                            "stroke_opacity": 0.25,
                            "stroke_width": 0.4,
                        }
                    ],
                },
            ],
            None,
            None,
            False,
            True,
            30,
        )
        self.assertEqual(svg.count("data:image/png;base64,"), 1)
        self.assertLess(svg.index('inkscape:label="Bottom raster"'), svg.index('inkscape:label="Top vector"'))
        self.assertIn('fill-opacity="0.5"', svg)
        self.assertIn('stroke-opacity="0.25"', svg)
        self.assertIn('transform="rotate(30 ', svg)
        north = svgdraw.build_svg(sheet, extent, [], None, None, False, True, 30)
        self.assertIn('transform="rotate(30 ', north)

        rotated = svgdraw.build_svg(
            sheet,
            extent,
            [
                {
                    "name": "Line",
                    "features": [
                        {
                            "kind": "line",
                            "points": [(0, 5), (10, 5)],
                            "stroke": "#1c4b3a",
                            "stroke_width": 0.4,
                        }
                    ],
                }
            ],
            None,
            None,
            False,
            False,
            90,
        )
        body = rotated.split('inkscape:label="Line"', 1)[1].split("</g>", 1)[0]
        path = re.search(r'<path d="([^"]*)"', body)
        self.assertIsNotNone(path)
        numbers = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", path.group(1))]
        xs = numbers[0::2]
        self.assertTrue(xs)
        self.assertTrue(all(abs(value - 210) < 0.01 for value in xs))

    def test_svg_no_fill(self) -> None:
        sheet = svgdraw.sheet_for_view(800, 600)
        extent = (0, 0, 10, 10)
        svg = svgdraw.build_svg(
            sheet,
            extent,
            [
                {
                    "name": "No fill",
                    "features": [
                        {
                            "kind": "polygon",
                            "rings": [[(1, 1), (9, 1), (9, 9), (1, 9)]],
                            "fill": "none",
                            "stroke": "#1c4b3a",
                            "stroke_width": 0.4,
                        }
                    ],
                }
            ],
            None,
            None,
            False,
            False,
        )
        self.assertIn('fill="none"', svg)
        self.assertNotIn("fill-opacity", svg)


if __name__ == "__main__":
    unittest.main()
