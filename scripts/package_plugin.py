"""Build the QGIS plugin zip for upload to plugins.qgis.org."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "hkmapservices"
INCLUDED_NAMES = {"datasets.json", "LICENSE", "README.md", "metadata.txt"}
INCLUDED_SUFFIXES = {".py", ".png", ".svg"}
SKIPPED_DIRECTORIES = {"tests", "scripts", ".git", "__pycache__", "dist"}


def main() -> None:
    version = _version(ROOT / "metadata.txt")
    destination = ROOT / "dist" / f"{PLUGIN}-{version}.zip"
    names = _package_names()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in names:
            archive.write(path, name)
    print(destination)


def _version(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("version="):
            version = line.split("=", 1)[1].strip()
            if version:
                return version
    raise SystemExit("metadata.txt has no version.")


def _package_names() -> list:
    chosen = []
    for directory, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIPPED_DIRECTORIES)
        folder = Path(directory)
        for filename in sorted(filenames):
            path = folder / filename
            if not _included(path):
                continue
            name = f"{PLUGIN}/{path.relative_to(ROOT).as_posix()}"
            if "\\" in name or not name.startswith(f"{PLUGIN}/"):
                raise SystemExit(f"Zip path must stay inside {PLUGIN}/: {name}")
            chosen.append((path, name))
    archived = {name for _path, name in chosen}
    required = {f"{PLUGIN}/__init__.py", f"{PLUGIN}/metadata.txt"}
    missing = required - archived
    if missing:
        raise SystemExit(f"The package is missing {', '.join(sorted(missing))}.")
    return chosen


def _included(path: Path) -> bool:
    if path.suffix.casefold() in {".pyc", ".zip"}:
        return False
    if path.name in INCLUDED_NAMES:
        return True
    return path.suffix.casefold() in INCLUDED_SUFFIXES


if __name__ == "__main__":
    main()
