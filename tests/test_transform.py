"""Tests for coordinate table files. The projection itself is checked with QGIS."""

from __future__ import annotations

import os
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

    def test_csv_encodings(self) -> None:
        traditional = "\u5099\u8a3b"
        simplified = "\u5907\u6ce8"
        self.assertEqual(_loaded_remark(traditional, "utf-8-sig"), traditional)
        self.assertEqual(_loaded_remark(traditional, "big5hkscs"), traditional)
        self.assertEqual(_loaded_remark(simplified, "gb18030"), simplified)
        self.assertEqual(_loaded_remark(traditional, "utf-16"), traditional)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "points.csv")
            Path(path).write_bytes(b"\xff")
            with self.assertRaises(ValueError) as caught:
                transform.read_csv_records(path)
        self.assertEqual(
            str(caught.exception),
            "The CSV encoding could not be read. Save the file as UTF-8 and try again.",
        )

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
        self.assertFalse(transform.is_coordinate_text("."))
        self.assertTrue(transform.is_coordinate_text(".5"))
        self.assertTrue(transform.is_coordinate_text("5."))
        self.assertFalse(transform.is_coordinate_text("1,000"))
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

    def test_preferred_pair_uses_the_selected_side(self) -> None:
        record = {
            "northing": "819069.8",
            "easting": "836694.05",
            "latitude": "22.31060243",
            "longitude": "114.18100933",
        }
        self.assertEqual(transform.preferred_pair("hkgrid", record), "hkgrid")
        self.assertEqual(transform.preferred_pair("wgsgeog", record), "wgsgeog")
        grid_only = dict(record, latitude="", longitude="")
        self.assertEqual(transform.preferred_pair("wgsgeog", grid_only), "hkgrid")
        self.assertIsNone(transform.preferred_pair("hkgrid", {"northing": ".", "easting": "1"}))
        with self.assertRaises(ValueError):
            transform.preferred_pair("middle", record)

    def test_publish_file_keeps_the_existing_file_when_writing_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "coordinates.xlsx"
            path.write_text("original", encoding="utf-8")
            self.assertFalse(transform.publish_file(str(path), lambda _temp: False))
            self.assertEqual(path.read_text(encoding="utf-8"), "original")
            self.assertEqual(sorted(name for name in os.listdir(directory)), ["coordinates.xlsx"])

            def write(temp: str) -> bool:
                Path(temp).write_text("replaced", encoding="utf-8")
                return True

            self.assertTrue(transform.publish_file(str(path), write))
            self.assertEqual(path.read_text(encoding="utf-8"), "replaced")
            self.assertEqual(sorted(name for name in os.listdir(directory)), ["coordinates.xlsx"])

    def test_degrees_minutes_seconds(self) -> None:
        self.assertEqual(transform.format_dms(22.310602434), "22\u00b0 18' 38.17\"")
        self.assertEqual(transform.format_dms(114.181009327), "114\u00b0 10' 51.63\"")
        self.assertEqual(transform.format_dms(-22.5), "-22\u00b0 30' 00.00\"")


def _loaded_remark(remark: str, encoding: str) -> str:
    header = "Point ID,Northing (m),Easting (m),Latitude,Longitude,Remarks"
    text = header + "\nA1,819069.800,836694.050,,," + remark + "\n"
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "points.csv")
        Path(path).write_bytes(text.encode(encoding))
        loaded = transform.read_csv_records(path)
    return loaded[0]["remarks"]


if __name__ == "__main__":
    unittest.main()
