"""Tests for URL names and resume choices. These do not download files."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import downloadmath


class DownloadMathTests(unittest.TestCase):
    def test_valid_url(self) -> None:
        self.assertTrue(downloadmath.is_valid_url("https://example.com/files/photo.tif"))
        self.assertTrue(downloadmath.is_valid_url("http://example.com/a.zip"))
        self.assertFalse(downloadmath.is_valid_url("file:///C:/photo.tif"))
        self.assertFalse(downloadmath.is_valid_url("not a url"))
        self.assertFalse(downloadmath.is_valid_url(""))

    def test_filename_from_content_disposition(self) -> None:
        header = 'attachment; filename="site photo.tif"'
        self.assertEqual(
            downloadmath.filename_from_disposition(header, "https://example.com/download?id=1"),
            "site photo.tif",
        )
        encoded = "attachment; filename*=UTF-8''plan%20sheet.zip"
        self.assertEqual(
            downloadmath.filename_from_disposition(encoded, "https://example.com/get"),
            "plan sheet.zip",
        )
        unquoted = "attachment; filename=plain.tif"
        self.assertEqual(
            downloadmath.filename_from_disposition(unquoted, "https://example.com/get"),
            "plain.tif",
        )
        self.assertEqual(
            downloadmath.filename_from_disposition("", "https://example.com/maps/base.tif"),
            "base.tif",
        )

    def test_column_without_addresses(self) -> None:
        urls, rejected = downloadmath.split_addresses(["1963-01-01", "01/02/1963"])
        self.assertEqual(urls, [])
        self.assertEqual(rejected, 2)
        urls, rejected = downloadmath.split_addresses(
            ["1963-01-01", "https://example.com/tiles/directDownload"]
        )
        self.assertEqual(urls, ["https://example.com/tiles/directDownload"])
        self.assertEqual(rejected, 1)

    def test_repeated_download_name(self) -> None:
        used = set()
        self.assertEqual(downloadmath.unique_filename("directDownload", used), "directDownload")
        self.assertEqual(downloadmath.unique_filename("directDownload", used), "directDownload_2")
        self.assertEqual(downloadmath.unique_filename("directDownload", used), "directDownload_3")
        self.assertEqual(downloadmath.unique_filename("sheet.tif", used), "sheet.tif")
        self.assertEqual(downloadmath.unique_filename("sheet.tif", used), "sheet_2.tif")
        self.assertEqual(downloadmath.unique_filename("SHEET.TIF", used), "SHEET_3.TIF")

    def test_stopped_message_omits_zero(self) -> None:
        self.assertEqual(
            downloadmath.stopped_message(3, 0),
            "Download stopped. 3 files downloaded.",
        )
        self.assertEqual(
            downloadmath.stopped_message(3, 2),
            "Download stopped. 3 files downloaded. 2 were already in the folder.",
        )
        self.assertNotIn("0", downloadmath.stopped_message(3, 0))

    def test_transfer_choice(self) -> None:
        self.assertEqual(downloadmath.transfer_choice(False, False, False), "start")
        self.assertEqual(downloadmath.transfer_choice(True, True, True), "skip")
        self.assertEqual(downloadmath.transfer_choice(True, False, True), "resume")
        self.assertEqual(downloadmath.transfer_choice(True, False, False), "restart")

    def test_content_range_validation(self) -> None:
        self.assertEqual(downloadmath.range_start("bytes 3-5/6"), 3)
        self.assertEqual(downloadmath.range_start("bytes 0-2/6"), 0)
        self.assertIsNone(downloadmath.range_start(""))
        self.assertIsNone(downloadmath.range_start("items 3-5/6"))
        self.assertEqual(downloadmath.range_total("bytes 3-5/6"), 6)
        self.assertIsNone(downloadmath.range_total("bytes 3-5/*"))
        self.assertTrue(downloadmath.resume_matches("bytes 3-5/6", 3))
        self.assertFalse(downloadmath.resume_matches("bytes 0-2/6", 3))
        self.assertFalse(downloadmath.resume_matches("", 3))

    def test_partial_sidecar_only_resumes_its_own_url(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            url = "https://example.com/sheet.tif"
            self.assertTrue(downloadmath.write_sidecar(str(folder), "sheet.tif", url, '"abc"', 6))
            record = downloadmath.read_sidecar(str(folder), "sheet.tif")
            self.assertIsNotNone(record)
            self.assertTrue(downloadmath.part_is_ours(record, url))
            self.assertFalse(downloadmath.part_is_ours(record, url + "?other"))
            self.assertFalse(downloadmath.part_is_stale(record, '"abc"', 6))
            self.assertTrue(downloadmath.part_is_stale(record, '"def"', 6))
            self.assertTrue(downloadmath.part_is_stale(record, '"abc"', 7))
            self.assertIsNone(downloadmath.read_sidecar(str(folder), "missing.tif"))
            self.assertFalse(downloadmath.part_is_ours(None, url))

    def test_discard_part_removes_only_its_own_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            part = Path(downloadmath.part_path(str(folder), "sheet.tif"))
            sidecar = Path(downloadmath.sidecar_path(str(folder), "sheet.tif"))
            part.write_bytes(b"partial")
            sidecar.write_text("{}", encoding="utf-8")
            other = folder / "keep.txt"
            other.write_text("keep", encoding="utf-8")
            downloadmath.discard_part(str(folder), "sheet.tif")
            self.assertFalse(part.exists())
            self.assertFalse(sidecar.exists())
            self.assertTrue(other.is_file())

    def test_publish_part_never_replaces_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            part = folder / "sheet.tif.part"
            final = folder / "sheet.tif"
            part.write_bytes(b"new")
            final.write_bytes(b"old")
            self.assertFalse(downloadmath.publish_part(str(part), str(final)))
            self.assertEqual(final.read_bytes(), b"old")
            self.assertEqual(part.read_bytes(), b"new")

    def test_publish_part_moves_the_file_into_place(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            part = folder / "sheet.tif.part"
            final = folder / "sheet.tif"
            part.write_bytes(b"new")
            self.assertTrue(downloadmath.publish_part(str(part), str(final)))
            self.assertEqual(final.read_bytes(), b"new")
            self.assertFalse(part.exists())

    def test_folder_index_ignores_letter_case_and_tracks_new_names(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            (folder / "SHEET.TIF").write_bytes(b"map")
            index = downloadmath.FolderIndex(str(folder))
            self.assertEqual(index.find("sheet.tif"), "SHEET.TIF")
            self.assertIsNone(index.find("other.tif"))
            index.add("other.tif")
            self.assertEqual(index.find("OTHER.TIF"), "other.tif")
            self.assertIn("Other.TIF", index)


if __name__ == "__main__":
    unittest.main()
