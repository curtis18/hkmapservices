"""Tests for service URLs and the Hong Kong 1980 tile grid."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hkmapservices import catalog
from hkmapservices import config


class CatalogTests(unittest.TestCase):
    def test_raster_urls(self) -> None:
        self.assertEqual(
            catalog.raster_url("topographic", "WGS84"),
            "https://mapapi.geodata.gov.hk/gs/api/v1.0.0/xyz/basemap/WGS84/{z}/{x}/{y}.png",
        )
        self.assertEqual(
            catalog.raster_url("imagery", "HK80"),
            "https://mapapi.geodata.gov.hk/gs/api/v1.0.0/xyz/imagery/HK80/{z}/{x}/{y}.png",
        )
        self.assertEqual(
            catalog.raster_url("label", "HK80", "tc"),
            "https://mapapi.geodata.gov.hk/gs/api/v1.0.0/xyz/label/hk/tc/HK80/{z}/{x}/{y}.png",
        )

    def test_vector_urls(self) -> None:
        self.assertEqual(
            catalog.vector_service_url("basemap", "HK80"),
            "https://mapapi.geodata.gov.hk/gs/api/v1.0.0/vt/basemap/HK80",
        )
        self.assertEqual(
            catalog.vector_style_url("label", "WGS84", "en"),
            "https://mapapi.geodata.gov.hk/gs/api/v1.0.0/vt/label/hk/en/WGS84/resources/styles/root.json",
        )
        self.assertEqual(catalog.vector_zoom(), (9, 15))

    def test_zoom_ranges(self) -> None:
        self.assertEqual(catalog.raster_zoom("topographic", "HK80"), (10, 20))
        self.assertEqual(catalog.raster_zoom("topographic", "WGS84"), (10, 20))
        self.assertEqual(catalog.raster_zoom("imagery", "HK80"), (7, 20))
        self.assertEqual(catalog.raster_zoom("imagery", "WGS84"), (0, 20))
        self.assertEqual(catalog.raster_zoom("label", "HK80"), (8, 20))
        self.assertEqual(catalog.raster_zoom("label", "WGS84"), (0, 20))

    def test_basemap_order_and_defaults(self) -> None:
        self.assertEqual(
            [key for key, _label, _tip in catalog.BASEMAPS],
            ["imagery", "topographic", "vector"],
        )
        self.assertEqual(
            [label for _key, label, _tip in catalog.BASEMAPS],
            ["Imagery Map", "Topographic Map", "Vector Map"],
        )
        self.assertEqual(catalog.CRS_CHOICES[0][0], "WGS84")
        self.assertEqual(catalog.default_label_kind("imagery"), "raster")
        self.assertEqual(catalog.default_label_kind("topographic"), "raster")
        self.assertEqual(catalog.default_label_kind("vector"), "vector")

    def test_dataset_definitions(self) -> None:
        self.assertEqual(
            [dataset.id for dataset in catalog.DATASETS],
            ["district", "libraries", "fitness", "outdoor", "traffic", "water", "barbecue"],
        )
        self.assertEqual(
            [dataset.title for dataset in catalog.datasets_for_dialog()],
            [
                "Barbecue Sites",
                "District Boundary",
                "Fitness Rooms",
                "Libraries",
                "Outdoor Fitness Equipment",
                "Traffic Detectors",
                "Water Dispensers",
            ],
        )
        self.assertTrue(all(dataset.attribution == catalog.CSDI_CREDIT for dataset in catalog.DATASETS))
        district = catalog.dataset_by_id("district")
        self.assertEqual(district.crs, "EPSG:2326")
        self.assertEqual(district.geometry, "polygon")
        self.assertEqual(district.name_field("en"), "NAME_EN")
        self.assertEqual(district.name_field("tc"), "NAME_TC")
        self.assertTrue(district.url.endswith("/had_rcd_1634523272907_75218/FeatureServer/0"))

        libraries = catalog.dataset_by_id("libraries")
        self.assertEqual(libraries.name_field("sc"), "NameTC")
        self.assertTrue(libraries.url.endswith("/lcsd_rcd_1629267205214_44807/FeatureServer/0"))

        fitness = catalog.dataset_by_id("fitness")
        self.assertEqual(fitness.marker, "circle")
        self.assertTrue(fitness.url.endswith("/lcsd_rcd_1629267205215_5008/FeatureServer/0"))

        outdoor = catalog.dataset_by_id("outdoor")
        self.assertEqual(outdoor.crs, "EPSG:4326")
        self.assertEqual(outdoor.marker, "square")
        self.assertEqual(outdoor.name_field("en"), "Name_en")
        self.assertEqual(outdoor.name_field("tc"), "Name_cn")
        self.assertEqual(outdoor.name_field("sc"), "Name_cn")
        self.assertEqual(outdoor.address_field("sc"), "Address_sc")
        self.assertTrue(outdoor.url.endswith("/lcsd_rcd_1670389505454_91091/FeatureServer/0"))

        traffic = catalog.dataset_by_id("traffic")
        self.assertEqual(traffic.crs, "EPSG:2326")
        self.assertEqual(traffic.geometry, "point")
        self.assertEqual(traffic.marker, "diamond")
        self.assertEqual(traffic.name_field("en"), "Road_EN")
        self.assertEqual(traffic.name_field("tc"), "Road_TC")
        self.assertEqual(traffic.name_field("sc"), "Road_SC")
        self.assertEqual(traffic.district_field("en"), "District")
        self.assertEqual(traffic.tip_fields, ("AID_ID_Number", "Direction"))
        self.assertTrue(traffic.url.endswith("/td_rcd_1671693191724_92214/FeatureServer/0"))
        self.assertEqual(
            traffic.portal_url,
            "https://portal.csdi.gov.hk/geoportal/?lang=en&datasetId=td_rcd_1671693191724_92214",
        )

        water = catalog.dataset_by_id("water")
        self.assertEqual(water.crs, "EPSG:4326")
        self.assertEqual(water.geometry, "point")
        self.assertEqual(water.marker, "triangle")
        self.assertEqual(water.name_field("en"), "Name_of_Building_or_Facility_EN")
        self.assertEqual(water.name_field("tc"), "Name_of_Building_or_Facility_TC")
        self.assertEqual(water.name_field("sc"), "Name_of_Building_or_Facility_SC")
        self.assertEqual(water.address_field("tc"), "Street_TC")
        self.assertEqual(water.district_field("sc"), "District_SC")
        self.assertTrue(water.url.endswith("/lcsd_rcd_1671530248519_33416/FeatureServer/0"))

        barbecue = catalog.dataset_by_id("barbecue")
        self.assertEqual(barbecue.crs, "EPSG:2326")
        self.assertEqual(barbecue.geometry, "point")
        self.assertEqual(barbecue.marker, "star")
        self.assertEqual(barbecue.color, "168,63,97,255")
        self.assertEqual(barbecue.name_field("en"), "SITE_NAME_EN")
        self.assertEqual(barbecue.name_field("tc"), "SITE_NAME_TC")
        self.assertEqual(barbecue.name_field("sc"), "SITE_NAME_TC")
        self.assertEqual(barbecue.address_field("en"), "BARBECUE_AREA_EN")
        self.assertEqual(barbecue.address_field("tc"), "BARBECUE_AREA_TC")
        self.assertEqual(barbecue.district_field("en"), "COUNTRY_PARK_EN")
        self.assertEqual(barbecue.district_field("tc"), "COUNTRY_PARK_TC")
        self.assertTrue(barbecue.url.endswith("/afcd_rcd_1635132752214_22438/FeatureServer/0"))

    def test_dataset_draw_order(self) -> None:
        self.assertEqual(catalog.dataset_draw_rank("polygon"), 0)
        self.assertEqual(catalog.dataset_draw_rank("line"), 1)
        self.assertEqual(catalog.dataset_draw_rank("point"), 2)
        ordered = catalog.datasets_bottom_to_top(("traffic", "district", "outdoor", "libraries"))
        self.assertEqual(
            [dataset.id for dataset in ordered],
            ["district", "libraries", "outdoor", "traffic"],
        )
        with_water = catalog.datasets_bottom_to_top(("water", "district", "traffic"))
        self.assertEqual([dataset.id for dataset in with_water], ["district", "traffic", "water"])
        with_barbecue = catalog.datasets_bottom_to_top(("barbecue", "water", "district"))
        self.assertEqual([dataset.id for dataset in with_barbecue], ["district", "water", "barbecue"])

    def test_central_hong_kong_tile_index(self) -> None:
        self.assertEqual(catalog.hk80_tile_index(836000, 810000, 11), (287, 385))
        span = catalog.hk80_tile_span(11)
        self.assertAlmostEqual(span / catalog.VECTOR_TILE_PIXELS, 38.218514, places=5)

    def test_hk80_raster_xml(self) -> None:
        url = catalog.raster_url("topographic", "HK80")
        xml = catalog.hk80_raster_xml(url)
        self.assertTrue(xml.startswith("<GDAL_WMS>"))
        self.assertIn("<Projection>EPSG:2326</Projection>", xml)
        self.assertIn("<UpperLeftX>-4786700.000000</UpperLeftX>", xml)
        self.assertIn("<UpperLeftY>8353100.000000</UpperLeftY>", xml)
        self.assertIn("<BlockSizeX>256</BlockSizeX>", xml)
        self.assertIn("<BlockSizeY>256</BlockSizeY>", xml)
        self.assertIn("<YOrigin>top</YOrigin>", xml)
        self.assertIn("<TileCountX>1</TileCountX>", xml)
        self.assertIn("<TileLevel>20</TileLevel>", xml)
        self.assertIn("<MaxConnections>4</MaxConnections>", xml)
        self.assertIn("<ZeroBlockHttpCodes>204,404</ZeroBlockHttpCodes>", xml)
        self.assertIn(url.replace("{", "${"), xml)
        lower_x = catalog.HK80_ORIGIN_X + catalog.HK80_LEVEL0_SPAN
        lower_y = catalog.HK80_ORIGIN_Y - catalog.HK80_LEVEL0_SPAN
        self.assertIn(f"<LowerRightX>{lower_x:.6f}</LowerRightX>", xml)
        self.assertIn(f"<LowerRightY>{lower_y:.6f}</LowerRightY>", xml)
        self.assertAlmostEqual(catalog.HK80_LEVEL0_SPAN, 40075016.685568, places=5)

    def test_hk80_raster_xml_escapes_special_characters(self) -> None:
        xml = catalog.hk80_raster_xml("https://example.test/tiles?a=1&b=\"2\"&c='3'")
        self.assertIn("a=1&amp;b=&quot;2&quot;&amp;c=&#x27;3&#x27;", xml)
        self.assertNotIn("a=1&b=", xml)

    def test_unknown_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            catalog.raster_url("satellite", "HK80")
        with self.assertRaises(ValueError):
            catalog.raster_url("label", "HK80", "fr")
        with self.assertRaises(ValueError):
            catalog.dataset_by_id("missing")
        with self.assertRaises(ValueError):
            catalog.project_crs_authid("HK63")
        with self.assertRaises(ValueError):
            catalog.default_label_kind("satellite")
        with self.assertRaises(ValueError):
            catalog.dataset_draw_rank("mesh")

    def test_shared_constants(self) -> None:
        self.assertEqual(config.HK1980, "EPSG:2326")
        self.assertEqual(config.WGS84, "EPSG:4326")
        self.assertEqual(config.WEB_MERCATOR, "EPSG:3857")
        self.assertEqual(catalog.API_ROOT, config.API_ROOT)
        self.assertEqual(catalog.FEATURE_ROOT, config.FEATURE_ROOT)
        self.assertEqual(catalog.CSDI_CREDIT, config.CSDI_CREDIT)
        self.assertEqual(catalog.project_crs_authid("HK80"), config.HK1980)
        self.assertEqual(catalog.project_crs_authid("WGS84"), config.WEB_MERCATOR)

    def test_dataset_file_rejects_an_unknown_crs(self) -> None:
        payload = [
            {
                "id": "sample",
                "title": "Sample",
                "service": "sample_service",
                "crs": "EPSG:9999",
                "geometry": "point",
                "marker": "circle",
                "color": "0,0,0,255",
                "name_fields": {"en": "NAME", "tc": "NAME", "sc": "NAME"},
                "address_fields": {},
                "district_fields": {},
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "datasets.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                catalog.load_datasets(str(path))

    def test_credits(self) -> None:
        self.assertEqual(catalog.basemap_credits("topographic"), (catalog.MAP_CREDIT,))
        imagery = catalog.basemap_credits("imagery")
        self.assertIn(catalog.MAP_CREDIT, imagery)
        self.assertIn(catalog.AERIAL_CREDIT, imagery)
        self.assertTrue(any("Landsat" in line for line in imagery))
        self.assertTrue(any("MODIS" in line for line in imagery))


if __name__ == "__main__":
    unittest.main()
