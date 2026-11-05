"""Tests for unpacking a download folder. These do not start QGIS."""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unzipfiles


class UnzipFolderTests(unittest.TestCase):
    def test_replaces_a_file_and_leaves_other_folders_alone(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            (folder / "sheet.tif").write_bytes(b"old")
            notes = folder / "notes"
            notes.mkdir()
            (notes / "keep.txt").write_text("stay", encoding="utf-8")
            self._write_zip(
                folder / "sheet.zip",
                {
                    "sheet/sheet.tif": b"new",
                    "sheet/nested/extra.txt": b"deep",
                },
            )
            text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertEqual((folder / "sheet.tif").read_bytes(), b"new")
            self.assertEqual((folder / "extra.txt").read_bytes(), b"deep")
            self.assertFalse((folder / "sheet").exists())
            self.assertTrue((folder / "sheet.zip").is_file())
            self.assertEqual((notes / "keep.txt").read_text(encoding="utf-8"), "stay")
            self.assertEqual(text, "Unzipped 1 zip file. Wrote 2 files. Replaced 1 file.")
            self.assertEqual(kind, "ok")
            self._no_staging(folder)

    def test_existing_folder_with_the_same_name_is_not_merged_or_removed(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            sheet = folder / "sheet"
            sheet.mkdir()
            (sheet / "keep.txt").write_text("user data", encoding="utf-8")
            self._write_zip(folder / "sheet.zip", {"sheet/new.txt": b"new"})
            text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertEqual((folder / "new.txt").read_bytes(), b"new")
            self.assertTrue(sheet.is_dir())
            self.assertEqual((sheet / "keep.txt").read_text(encoding="utf-8"), "user data")
            self.assertFalse((folder / "keep.txt").exists())
            self.assertEqual(kind, "ok")
            self.assertIn("Unzipped 1 zip file.", text)

    def test_later_zip_replaces_the_same_name(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            self._write_zip(folder / "first.zip", {"tile/sheet.tif": b"first"})
            self._write_zip(folder / "second.zip", {"tile/sheet.tif": b"second"})
            text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertEqual((folder / "sheet.tif").read_bytes(), b"second")
            self.assertFalse((folder / "tile").exists())
            self.assertTrue((folder / "first.zip").is_file())
            self.assertTrue((folder / "second.zip").is_file())
            self.assertEqual(text, "Unzipped 2 zip files. Wrote 2 files. Replaced 1 file.")
            self.assertEqual(kind, "ok")

    def test_duplicate_final_names_fail_without_writing_anything(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            self._write_zip(
                folder / "dup.zip",
                {
                    "a/one.txt": b"a",
                    "b/one.txt": b"b",
                    "m/one.txt": b"m",
                },
            )
            text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertFalse((folder / "one.txt").exists())
            self.assertTrue((folder / "dup.zip").is_file())
            self.assertIn("duplicate file name: one.txt", text)
            self.assertEqual(kind, "error")
            self._no_staging(folder)

    def test_case_only_duplicate_names_fail(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            self._write_zip(folder / "dup.zip", {"A.TXT": b"upper", "a.txt": b"lower"})
            text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertFalse((folder / "A.TXT").exists())
            self.assertIn("duplicate file name", text)
            self.assertEqual(kind, "error")

    def test_alternate_data_stream_name_is_rejected(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            self._write_zip(folder / "ads.zip", {"data.txt:secret": b"hidden"})
            text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertFalse((folder / "data.txt").exists())
            self.assertIn("unsafe name", text)
            self.assertEqual(kind, "error")

    def test_windows_reserved_and_trailing_names_are_rejected(self) -> None:
        for member in ("con.txt", "aux", "nul.txt", "com1.txt", "trail.", "trail "):
            with self.subTest(member=member), self._folder() as raw:
                folder = Path(raw)
                self._write_zip(folder / "bad.zip", {member: b"data"})
                text, kind = unzipfiles.unzip_folder(str(folder))
                self.assertIn("unsafe name", text)
                self.assertEqual(kind, "error")
                self.assertFalse((folder / "data.txt").exists())

    def test_symbolic_link_member_is_rejected(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            with zipfile.ZipFile(folder / "link.zip", "w") as archive:
                info = zipfile.ZipInfo("link.txt")
                info.external_attr = 0o120777 << 16
                archive.writestr(info, "target")
            text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertFalse((folder / "link.txt").exists())
            self.assertIn("symbolic links", text)
            self.assertEqual(kind, "error")

    def test_parent_path_is_not_written(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            outside = folder.parent / "hkmap-unzip-escape.txt"
            if outside.exists():
                outside.unlink()
            path = folder / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("inside.txt", b"yes")
                archive.writestr(zipfile.ZipInfo("../hkmap-unzip-escape.txt"), b"nope")
            text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertFalse((folder / "inside.txt").exists())
            self.assertFalse(outside.exists())
            self.assertTrue(path.is_file())
            self.assertIn("unsafe path", text)
            self.assertEqual(kind, "error")

    def test_a_file_and_a_folder_with_the_same_name_are_rejected(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            self._write_zip(folder / "bad.zip", {"same": b"file", "same/child.txt": b"child"})
            text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertFalse((folder / "same").exists())
            self.assertIn("file and a folder share a name", text)
            self.assertEqual(kind, "error")

    def test_source_zip_is_never_replaced(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            self._write_zip(folder / "bundle.zip", {"bundle.zip": b"not the archive"})
            before = (folder / "bundle.zip").read_bytes()
            text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertEqual((folder / "bundle.zip").read_bytes(), before)
            self.assertIn("would replace a zip file", text)
            self.assertEqual(kind, "error")

    def test_partial_download_is_never_replaced(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            (folder / "photo.tif.part").write_bytes(b"partial")
            (folder / "photo.tif.part.json").write_text("{}", encoding="utf-8")
            self._write_zip(folder / "photo.zip", {"photo.tif.part": b"zip"})
            text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertEqual((folder / "photo.tif.part").read_bytes(), b"partial")
            self.assertIn("would replace a partial download", text)
            self.assertEqual(kind, "error")

    def test_corrupt_zip_leaves_no_partial_files(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            (folder / "bad.zip").write_bytes(b"not a zip file")
            text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertIn("not a valid zip file", text)
            self.assertEqual(kind, "error")
            self._no_staging(folder)

    def test_member_limit_is_enforced(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            self._write_zip(
                folder / "many.zip",
                {"one.txt": b"1", "two.txt": b"2", "three.txt": b"3"},
            )
            with mock.patch.object(unzipfiles, "_MAX_MEMBERS", 2):
                text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertIn("too many files", text)
            self.assertEqual(kind, "error")

    def test_free_space_is_checked_before_writing(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            self._write_zip(folder / "big.zip", {"big/file.bin": b"x" * 1024})
            with mock.patch.object(unzipfiles, "_free_bytes", return_value=1):
                text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertIn("not enough free space", text)
            self.assertEqual(kind, "error")
            self.assertFalse((folder / "file.bin").exists())
            self._no_staging(folder)

    def test_empty_zip_is_unzipped_without_a_file_count(self) -> None:
        with self._folder() as raw:
            folder = Path(raw)
            with zipfile.ZipFile(folder / "empty.zip", "w"):
                pass
            text, kind = unzipfiles.unzip_folder(str(folder))
            self.assertEqual(text, "Unzipped 1 zip file.")
            self.assertEqual(kind, "ok")

    def test_folder_without_zips(self) -> None:
        with self._folder() as raw:
            text, kind = unzipfiles.unzip_folder(raw)
            self.assertEqual(text, "The folder has no zip files.")
            self.assertEqual(kind, "error")

    def _folder(self):
        return tempfile.TemporaryDirectory()

    def _no_staging(self, folder: Path) -> None:
        self.assertEqual(
            [name for name in folder.iterdir() if name.name.startswith(".hkmap-unzip-")],
            [],
        )

    def _write_zip(self, path: Path, members: dict) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)


if __name__ == "__main__":
    unittest.main()
