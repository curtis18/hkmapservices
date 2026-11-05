"""Unzip a save folder and put the extracted files beside the zip files.

Every archive is unpacked into a staging folder inside the save folder, then
each file is moved into place with ``os.replace``. A file that is already
there is replaced, which is intentional for files downloaded from CSDI. A
folder that existed before the run is never walked, changed, or removed.

This module does not import QGIS.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import zipfile
import zlib


_STAGING_PREFIX = ".hkmap-unzip-"
_COPY_BUFFER = 1024 * 1024
_MAX_MEMBERS = 200_000
_MAX_BYTES = 16 * 1024 * 1024 * 1024
_FREE_SPACE_MARGIN = 1024 * 1024
_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_INVALID_CHARACTERS = set('<>:"|?*')


class _PlanError(Exception):
    """A zip that cannot be unpacked without breaking the folder."""


def list_zip_files(folder: str) -> list:
    """Return zip file names that sit directly in the folder."""
    names = []
    for entry in os.scandir(folder):
        if not entry.name.casefold().endswith(".zip"):
            continue
        if entry.is_file():
            names.append(entry.name)
    names.sort(key=str.casefold)
    return names


def unzip_folder(folder: str, progress=None) -> tuple:
    """Extract every top-level zip and replace files with the same final name.

    The zip files stay in the folder and are never overwritten. An existing
    folder with the same name as a folder inside a zip is left untouched.
    """
    try:
        names = list_zip_files(folder)
    except OSError:
        return "The folder could not be read.", "error"
    if not names:
        return "The folder has no zip files.", "error"
    existing = _existing_entries(folder)
    unzipped = 0
    written = 0
    replaced = 0
    failed = 0
    reasons = []
    total = len(names)
    for index, name in enumerate(names, start=1):
        _progress(progress, f"Unzipping {index} of {total}: {name}")
        files, changes, reason = _unzip_one(
            os.path.join(folder, name),
            folder,
            existing,
            progress,
            index,
            total,
            name,
        )
        written += files
        replaced += changes
        if reason:
            failed += 1
            reasons.append(f"{name}: {reason}")
        else:
            unzipped += 1
            existing = _existing_entries(folder)
    return _finished_message(unzipped, written, replaced, failed, total, reasons)


def _unzip_one(path: str, folder: str, existing: dict, progress, index: int, total: int, label: str) -> tuple:
    try:
        staging = tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=folder)
    except OSError as exc:
        return 0, 0, _os_reason(exc)
    try:
        with zipfile.ZipFile(path) as archive:
            planned = _plan_archive(archive, staging, folder, existing)
            _extract_archive(archive, planned, folder, progress, index, total, label)
        return _publish_files(planned, folder, existing)
    except _PlanError as exc:
        return 0, 0, str(exc)
    except zipfile.BadZipFile:
        return 0, 0, "not a valid zip file"
    except NotImplementedError:
        return 0, 0, "unsupported compression"
    except (EOFError, zlib.error):
        return 0, 0, "damaged data"
    except RuntimeError as exc:
        text = str(exc).casefold()
        return 0, 0, "encrypted archive" if "encrypt" in text else _short_reason(exc)
    except OSError as exc:
        return 0, 0, _os_reason(exc)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _plan_archive(archive: zipfile.ZipFile, staging: str, folder: str, existing: dict) -> list:
    """Check the whole archive before writing any of it."""
    planned = []
    seen = {}
    staging_files = set()
    declared = 0
    infos = archive.infolist()
    if len(infos) > _MAX_MEMBERS:
        raise _PlanError(f"too many files ({len(infos)})")
    for info in infos:
        if _is_symlink(info):
            raise _PlanError("symbolic links are not allowed")
        relative = _relative_member(info.filename)
        if relative is None:
            raise _PlanError(f"unsafe path: {info.filename}")
        if not relative:
            continue
        for part in relative:
            problem = _name_problem(part)
            if problem:
                raise _PlanError(f"unsafe name: {info.filename} ({problem})")
        if info.is_dir() or str(info.filename).endswith("/"):
            continue
        final = relative[-1]
        key = final.casefold()
        if key in seen:
            raise _PlanError(f"duplicate file name: {final}")
        seen[key] = info.filename
        problem = _target_problem(final, existing)
        if problem:
            raise _PlanError(problem)
        staging_path = os.path.join(staging, *relative)
        staging_files.add(os.path.normcase(staging_path))
        declared += max(0, int(info.file_size))
        if declared > _MAX_BYTES:
            raise _PlanError("the archive is too large")
        planned.append((info, staging_path, final))
    if len(planned) > _MAX_MEMBERS:
        raise _PlanError(f"too many files ({len(planned)})")
    for _info, staging_path, _final in planned:
        parent = os.path.dirname(staging_path)
        while os.path.normcase(parent) != os.path.normcase(staging):
            if os.path.normcase(parent) in staging_files:
                raise _PlanError("a file and a folder share a name")
            parent = os.path.dirname(parent)
    replaced = 0
    for _info, _staging_path, final in planned:
        entry = existing.get(final.casefold())
        if entry is not None:
            replaced += entry["size"]
    required = declared + replaced + _FREE_SPACE_MARGIN
    free = _free_bytes(folder)
    if free is not None and required > free:
        raise _PlanError("not enough free space")
    return planned


def _extract_archive(archive: zipfile.ZipFile, planned: list, folder: str, progress, index: int, total: int, label: str) -> None:
    written = 0
    actual = 0
    checked_at = 0
    free = _free_bytes(folder)
    for info, staging_path, _final in planned:
        try:
            os.makedirs(os.path.dirname(staging_path), exist_ok=True)
            with archive.open(info, "r") as source, open(staging_path, "wb") as target:
                while True:
                    chunk = source.read(_COPY_BUFFER)
                    if not chunk:
                        break
                    actual += len(chunk)
                    if actual > _MAX_BYTES:
                        raise _PlanError("the archive is too large")
                    if free is not None and actual + _FREE_SPACE_MARGIN > free:
                        raise _PlanError("not enough free space")
                    if free is not None and actual - checked_at >= 64 * _COPY_BUFFER:
                        checked_at = actual
                        current = _free_bytes(folder)
                        if current is not None and current < _FREE_SPACE_MARGIN:
                            raise _PlanError("not enough free space")
                    target.write(chunk)
        except _PlanError:
            raise
        except (zipfile.BadZipFile, EOFError, zlib.error):
            raise _PlanError("damaged data") from None
        except NotImplementedError:
            raise _PlanError("unsupported compression") from None
        except RuntimeError as exc:
            text = str(exc).casefold()
            if "encrypt" in text:
                raise _PlanError("encrypted archive") from None
            raise _PlanError(_short_reason(exc)) from None
        except OSError as exc:
            raise _PlanError(_os_reason(exc)) from None
        written += 1
        if progress is not None and (written == len(planned) or written % 25 == 0):
            _progress(
                progress,
                f"Unzipping {index} of {total}: {label} ({written}/{len(planned)})",
            )


def _publish_files(planned: list, folder: str, existing: dict) -> tuple:
    written = 0
    replaced = 0
    for _info, staging_path, final in planned:
        entry = existing.get(final.casefold())
        target = os.path.join(folder, final)
        try:
            os.replace(staging_path, target)
        except OSError as exc:
            return written, replaced, f"could not write {final}: {_os_reason(exc)}"
        written += 1
        if entry is not None:
            replaced += 1
    return written, replaced, ""


def _existing_entries(folder: str) -> dict:
    """Return file and folder entries by case-folded name."""
    entries = {}
    try:
        with os.scandir(folder) as scan:
            for entry in scan:
                if entry.name.startswith(_STAGING_PREFIX):
                    continue
                try:
                    is_folder = entry.is_dir(follow_symlinks=False)
                    size = entry.stat(follow_symlinks=False).st_size
                except OSError:
                    is_folder = False
                    size = 0
                entries.setdefault(
                    entry.name.casefold(),
                    {"name": entry.name, "folder": is_folder, "size": size},
                )
    except OSError:
        pass
    return entries


def _target_problem(final: str, existing: dict):
    key = final.casefold()
    if key.startswith(_STAGING_PREFIX):
        return f"reserved name: {final}"
    entry = existing.get(key)
    if entry is None:
        return None
    if entry["folder"]:
        return f"a folder has that name: {entry['name']}"
    if key.endswith(".zip"):
        return f"would replace a zip file: {entry['name']}"
    if key.endswith(".part") or key.endswith(".part.json"):
        return f"would replace a partial download: {entry['name']}"
    return None


def _relative_member(name: str):
    """Return path parts that stay under the save folder, or None when one would leave it."""
    text = str(name or "").replace("\\", "/")
    if not text or text.startswith("/") or "\x00" in text:
        return None
    parts = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)
    return parts


def _name_problem(part: str):
    if part in (".", ".."):
        return "relative path"
    if not part.strip():
        return "empty name"
    if part != part.rstrip(" ."):
        return "trailing dot or space"
    if any(character in _INVALID_CHARACTERS for character in part):
        return "invalid character"
    if any(ord(character) < 32 for character in part):
        return "control character"
    stem = part.split(".", 1)[0].strip().casefold()
    if stem in _RESERVED_NAMES:
        return "reserved name"
    return None


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _progress(progress, text: str) -> None:
    if progress is not None:
        progress(text, "ok")


def _short_reason(exc) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return text[:160] if text else type(exc).__name__


def _os_reason(exc: OSError) -> str:
    return str(exc.strerror or _short_reason(exc))


def _free_bytes(folder: str):
    try:
        return shutil.disk_usage(folder).free
    except OSError:
        return None


def _finished_message(unzipped: int, written: int, replaced: int, failed: int, total: int, reasons: list) -> tuple:
    parts = []
    if unzipped:
        parts.append(f"Unzipped {unzipped} {_noun(unzipped, 'zip file', 'zip files')}.")
    if written:
        parts.append(f"Wrote {written} {_noun(written, 'file', 'files')}.")
    if replaced:
        parts.append(f"Replaced {replaced} {_noun(replaced, 'file', 'files')}.")
    if failed:
        if total == 1:
            parts.append("The zip file failed.")
        else:
            parts.append(f"{failed} of {total} zip files failed.")
        if reasons:
            parts.append(f"First: {reasons[0]}.")
            if len(reasons) > 1:
                parts.append(f"Also: {reasons[1]}.")
    if not parts:
        return "The folder has no zip files.", "error"
    if unzipped:
        return " ".join(parts), "warn" if failed else "ok"
    return " ".join(parts), "error"


def _noun(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural
