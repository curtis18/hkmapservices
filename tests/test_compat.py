"""Tests for the Qt 5 and Qt 6 compatibility helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import compat


class _Scoped:
    NoError = "scoped"


class _Owner:
    WriterError = _Scoped
    NoError = "flat"


class _Legacy:
    NoError = "flat"


class _CurrentDialog:
    def exec(self):
        return 7


class _LegacyDialog:
    def exec_(self):
        return 9


class CompatTests(unittest.TestCase):
    def test_scoped_member_wins(self) -> None:
        self.assertEqual(compat.enum_member(_Owner, "WriterError", "NoError"), "scoped")

    def test_flat_member_is_the_fallback(self) -> None:
        self.assertEqual(compat.enum_member(_Legacy, "WriterError", "NoError"), "flat")

    def test_exec_dialog_prefers_exec(self) -> None:
        self.assertEqual(compat.exec_dialog(_CurrentDialog()), 7)

    def test_exec_dialog_falls_back_to_exec_underscore(self) -> None:
        self.assertEqual(compat.exec_dialog(_LegacyDialog()), 9)


if __name__ == "__main__":
    unittest.main()
