"""Tests for the Lands Department location search response."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import locations


class LocationTests(unittest.TestCase):
    def test_cultural_centre_query(self) -> None:
        self.assertEqual(
            locations.search_url("cultural centre"),
            "https://www.map.gov.hk/gs/api/v1.0.0/locationSearch?q=cultural%20centre",
        )
        parsed = locations.parse_results(
            [
                {
                    "addressZH": "梳士巴利道 10號",
                    "nameZH": "香港文化中心",
                    "districtZH": "油尖旺區",
                    "x": 835599.0,
                    "y": 817190.0,
                    "nameEN": "HONG KONG CULTURAL CENTRE",
                    "addressEN": "10 SALISBURY ROAD ",
                    "districtEN": "Yau Tsim Mong District",
                },
                {
                    "nameEN": "Second place",
                    "nameZH": "第二地點",
                    "addressEN": "",
                    "addressZH": "",
                    "districtEN": "Islands District",
                    "districtZH": "離島區",
                    "x": 803285,
                    "y": 813131,
                },
            ]
        )
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["name_en"], "HONG KONG CULTURAL CENTRE")
        self.assertEqual(parsed[0]["name_zh"], "香港文化中心")
        self.assertEqual(parsed[0]["address_en"], "10 SALISBURY ROAD")
        self.assertEqual(parsed[0]["easting"], 835599.0)
        self.assertEqual(parsed[0]["northing"], 817190.0)
        self.assertEqual(parsed[1]["easting"], 803285.0)

    def test_blank_query_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            locations.search_url("  ")

    def test_non_list_payload_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            locations.parse_results({"nameEN": "Not a list"})


if __name__ == "__main__":
    unittest.main()
