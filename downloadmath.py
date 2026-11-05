"""URL names and resume choices for file downloads.

This module does not import QGIS.
"""

from __future__ import annotations

import json
import os
from urllib.parse import unquote, urlparse


PART_SUFFIX = ".part"
SIDECAR_SUFFIX = ".part.json"
_SIDECAR_APP = "hkmapservices"
_SIDECAR_FORMAT = 1


def is_valid_url(value) -> bool:
    """Return whether a cell contains an HTTP or HTTPS address."""
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def filename_from_disposition(header: str, url: str) -> str:
    """Return a file name from Content-Disposition, or from the address."""
    name = _disposition_name(header or "")
    if not name:
        name = os.path.basename(unquote(urlparse(str(url)).path))
    name = name.replace("\\", "/").split("/")[-1]
    cleaned = "".join("_" if character in '<>:"/\\|?*' else character for character in name).strip().rstrip(".")
    return cleaned or "download"


def split_addresses(values) -> tuple:
    """Return web addresses and how many other cells were left out."""
    urls = []
    rejected = 0
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if is_valid_url(text):
            urls.append(text)
        else:
            rejected += 1
    return urls, rejected


def unique_filename(name: str, used: set) -> str:
    """Return a file name that has not been claimed yet, ignoring letter case."""
    cleaned = name or "download"
    key = cleaned.casefold()
    if key not in used:
        used.add(key)
        return cleaned
    stem, suffix = _split_extension(cleaned)
    number = 2
    while True:
        candidate = f"{stem}_{number}{suffix}"
        key = candidate.casefold()
        if key not in used:
            used.add(key)
            return candidate
        number += 1


def stopped_message(saved: int, skipped: int) -> str:
    """Describe a cancelled download without mentioning a zero count."""
    parts = ["Download stopped."]
    if saved:
        noun = "file" if saved == 1 else "files"
        parts.append(f"{saved} {noun} downloaded.")
    if skipped:
        parts.append(_already_message(skipped))
    return " ".join(parts)


def finished_message(saved: int, skipped: int, failed: int, total: int) -> tuple:
    """Describe a finished download. Return the text and its status kind."""
    if failed and saved == 0 and skipped == 0:
        return f"{failed} of {total} files failed.", "error"
    parts = []
    if failed:
        parts.append(f"Downloaded {saved} of {total}. {failed} failed.")
        if skipped:
            parts.append(_already_message(skipped))
        return " ".join(parts), "warn"
    if skipped and not saved:
        return _already_message(skipped), "ok"
    noun = "file" if saved == 1 else "files"
    text = f"Downloaded {saved} {noun}."
    if skipped:
        text = f"{text} {_already_message(skipped)}"
    return text, "ok"


def _already_message(skipped: int) -> str:
    if skipped == 1:
        return "1 was already in the folder."
    return f"{skipped} were already in the folder."


def _split_extension(name: str) -> tuple:
    if "." not in name or name.startswith("."):
        return name, ""
    stem, suffix = name.rsplit(".", 1)
    if not stem:
        return name, ""
    return stem, f".{suffix}"


def transfer_choice(exists: bool, complete: bool, accepts_ranges: bool) -> str:
    """Return skip, resume, restart, or start."""
    if not exists:
        return "start"
    if complete:
        return "skip"
    if accepts_ranges:
        return "resume"
    return "restart"


def range_start(header: str):
    """Return the first byte in a Content-Range header, or None."""
    text = str(header or "").strip()
    lowered = text.casefold()
    if not lowered.startswith("bytes "):
        return None
    span = text[6:].split("/", 1)[0].strip()
    start, separator, _end = span.partition("-")
    if separator and start.isdigit():
        return int(start)
    return None


def range_total(header: str):
    """Return the full length in a Content-Range header, or None."""
    text = str(header or "").strip()
    if not text.casefold().startswith("bytes ") or "/" not in text:
        return None
    total = text.rsplit("/", 1)[-1].strip()
    return int(total) if total.isdigit() else None


def resume_matches(header: str, offset: int) -> bool:
    """Return whether a 206 response starts where the partial file ends."""
    start = range_start(header)
    return start is not None and start == int(offset)


def part_path(folder: str, name: str) -> str:
    """Return the working name used while a download is incomplete."""
    return os.path.join(folder, f"{name}{PART_SUFFIX}")


def sidecar_path(folder: str, name: str) -> str:
    """Return the record that says which download owns a ``.part`` file."""
    return part_path(folder, name) + ".json"


def write_sidecar(folder: str, name: str, url: str, etag: str = "", length: int = -1) -> bool:
    """Record the address and response that the partial file belongs to."""
    path = sidecar_path(folder, name)
    temporary = f"{path}.tmp"
    try:
        record = {
            "app": _SIDECAR_APP,
            "format": _SIDECAR_FORMAT,
            "url": str(url),
            "etag": str(etag or ""),
            "length": int(length),
        }
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, sort_keys=True)
        os.replace(temporary, path)
        return True
    except (OSError, TypeError, ValueError):
        try:
            os.remove(temporary)
        except OSError:
            pass
        return False


def read_sidecar(folder: str, name: str):
    """Return the record beside a partial file, or None when there is none."""
    try:
        with open(sidecar_path(folder, name), "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(record, dict):
        return None
    url = record.get("url")
    if not isinstance(url, str) or not url:
        return None
    return record


def part_is_ours(record, url: str) -> bool:
    """Return whether a partial file was written by this plugin for this URL."""
    if not isinstance(record, dict):
        return False
    if record.get("app") != _SIDECAR_APP or record.get("format") != _SIDECAR_FORMAT:
        return False
    return str(record.get("url") or "") == str(url)


def part_is_stale(record, etag: str, length: int) -> bool:
    """Return whether a partial file is ours but came from an older response."""
    if not isinstance(record, dict):
        return False
    old_etag = str(record.get("etag") or "")
    new_etag = str(etag or "")
    if old_etag and new_etag and old_etag != new_etag:
        return True
    try:
        old_length = int(record.get("length", -1))
    except (TypeError, ValueError):
        old_length = -1
    try:
        new_length = int(length)
    except (TypeError, ValueError):
        new_length = -1
    return old_length >= 0 and new_length >= 0 and old_length != new_length


def discard_part(folder: str, name: str) -> None:
    """Remove a partial file and its record. Other files are left alone."""
    for path in (part_path(folder, name), sidecar_path(folder, name)):
        try:
            os.remove(path)
        except OSError:
            pass


def discard_sidecar(folder: str, name: str) -> None:
    try:
        os.remove(sidecar_path(folder, name))
    except OSError:
        pass


def publish_part(part: str, final: str) -> bool:
    """Move a complete partial file into place, without replacing another file.

    The target name is claimed with an exclusive link (or an exclusive create
    when links are not supported), so a file that appears after planning is
    never overwritten.
    """
    try:
        os.link(part, final)
    except FileExistsError:
        return False
    except (OSError, AttributeError, NotImplementedError):
        return _publish_by_claim(part, final)
    try:
        os.remove(part)
    except OSError:
        pass
    return True


def _publish_by_claim(part: str, final: str) -> bool:
    try:
        handle = os.open(final, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    except OSError:
        return False
    os.close(handle)
    try:
        os.replace(part, final)
    except OSError:
        try:
            os.remove(final)
        except OSError:
            pass
        return False
    return True


class FolderIndex:
    """Case-insensitive names in one folder, updated as this run claims names."""

    def __init__(self, folder: str):
        self._names = {}
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    self._names.setdefault(entry.name.casefold(), entry.name)
        except OSError:
            pass

    def find(self, name: str):
        """Return the existing entry matching a name, or None."""
        return self._names.get(str(name).casefold())

    def add(self, name: str) -> None:
        self._names[str(name).casefold()] = name

    def __contains__(self, name: str) -> bool:
        return self.find(name) is not None


def _disposition_name(header: str) -> str:
    lowered = header.casefold()
    for marker in ("filename*=utf-8''", "filename*=UTF-8''"):
        start = header.casefold().find(marker.casefold())
        if start >= 0:
            value = header[start + len(marker):].split(";", 1)[0].strip().strip('"').strip("'")
            if value:
                return unquote(value)
    filename_parameter = "filename="
    start = lowered.find(filename_parameter)
    if start < 0:
        return ""
    value = header[start + len(filename_parameter):].split(";", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    return unquote(value.strip())
