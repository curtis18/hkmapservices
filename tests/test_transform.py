"""Tests for coordinate table files. The projection itself is checked with QGIS."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import transform


class TransformTests(unittest.TestCase):
    def test_header_aliases(self) -> None:
        record = transform.normalize_record(
            {
                "Point ID": "A1",
                "EASTING (m)": "836694.05",
                "Northing": "819069.8",
                "Lat": "",
                "LONGITUDE": None,
                "Remarks": "site note",
                "Ignored": "no",
            }
        )
        self.assertEqual(record["point_id"], "A1")
        self.assertEqual(record["easting"], "836694.05")
        self.assertEqual(record["northing"], "819069.8")
        self.assertEqual(record["latitude"], "")
        self.assertEqual(record["longitude"], "")
        self.assertEqual(record["remarks"], "site note")

    def test_csv_column_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "points.csv")
            transform.write_csv_records(path, [transform.TEMPLATE_RECORD])
            text = Path(path).read_text(encoding="utf-8")
            loaded = transform.read_csv_records(path)
        self.assertTrue(text.startswith("Point ID,Northing (m),Easting (m),Latitude,Longitude,Remarks"))
        self.assertEqual(loaded, [transform.TEMPLATE_RECORD])

    def test_rounded_decimals(self) -> None:
        self.assertEqual(transform.format_rounded(819726.721, 0), "819727")
        self.assertEqual(transform.format_rounded(2.5, 0), "3")
        self.assertEqual(transform.format_rounded(22.31651605, 6), "22.316516")

    def test_coordinate_text(self) -> None:
        self.assertTrue(transform.is_coordinate_text(""))
        self.assertTrue(transform.is_coordinate_text("22.13"))
        self.assertTrue(transform.is_coordinate_text("836694.050"))
        self.assertFalse(transform.is_coordinate_text("asdf"))
        self.assertFalse(transform.is_coordinate_text("22.13ds"))
        self.assertFalse(transform.is_coordinate_text("112.asdf"))
        self.assertFalse(transform.is_coordinate_text("-1"))
        self.assertFalse(transform.is_coordinate_text("1.2.3"))
        cleaned, skipped = transform.sanitize_records(
            [
                {
                    "point_id": "bad text stays",
                    "northing": "asdf",
                    "easting": "adsf",
                    "latitude": "22.13ds",
                    "longitude": "112.asdf",
                }
            ]
        )
        self.assertEqual(skipped, 4)
        self.assertEqual(cleaned[0]["point_id"], "bad text stays")
        self.assertEqual(cleaned[0]["northing"], "")
        self.assertEqual(cleaned[0]["latitude"], "")

    def test_degrees_minutes_seconds(self) -> None:
        self.assertEqual(transform.format_dms(22.310602434), "22\u00b0 18' 38.17\"")
        self.assertEqual(transform.format_dms(114.181009327), "114\u00b0 10' 51.63\"")
        self.assertEqual(transform.format_dms(-22.5), "-22\u00b0 30' 00.00\"")


if __name__ == "__main__":
    unittest.main()
