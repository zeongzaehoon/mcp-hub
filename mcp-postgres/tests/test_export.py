"""CSV/XLSX building is engine-agnostic: exotic values survive a round trip."""

import csv
import datetime as dt
import io
import uuid
from decimal import Decimal

import polars as pl

from mcpcluster_common import transform as tf
from mcpcluster_common.export import safe_filename, to_csv_bytes, to_xlsx_bytes

ROWS = [{"id": uuid.UUID(int=7), "amount": Decimal("12345.678901"), "big": 9007199254740993, "at": dt.datetime(2026, 9, 1, 10, 0, 0, 123000), "tags": ["a", "b"], "meta": {"k": [1, None]}, "ko": "한글", "empty": None}]


def test_csv_roundtrip_with_bom_and_nested_json():
    data = to_csv_bytes(tf.build_frame(ROWS))
    assert data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    assert rows[0]["id"] == str(uuid.UUID(int=7)) and rows[0]["amount"] == "12345.678901" and rows[0]["big"] == "9007199254740993"
    assert rows[0]["at"].startswith("2026-09-01T10:00:00.123") and rows[0]["ko"] == "한글" and rows[0]["empty"] == ""
    assert rows[0]["tags"] == '["a", "b"]' and rows[0]["meta"] == '{"k": [1, null]}'


def test_xlsx_roundtrip():
    data = to_xlsx_bytes(tf.build_frame(ROWS))
    back = pl.read_excel(io.BytesIO(data))
    assert back.height == 1 and back["ko"][0] == "한글"
    assert str(back["big"][0]) == "9007199254740993"  # > 2^53 is written as text, never rounded by Excel
    assert str(back["amount"][0]) == "12345.678901"


def test_filenames_are_sanitised():
    assert safe_filename("../../etc/passwd; rm -rf", "csv", "x") == "etc_passwd_rm_-rf.csv"
    assert safe_filename(None, "xlsx", "postgres-export") == "postgres-export.xlsx"
    assert safe_filename("report.csv", "csv", "x") == "report.csv"


def test_formula_injection_is_neutralized_but_numbers_are_not():
    rows = [{"v": "=HYPERLINK(\"http://evil\",\"x\")"}, {"v": "+1+1"}, {"v": "-2+3"}, {"v": "@SUM(A1)"}, {"v": "\tcmd"},
            {"v": "-9007199254740993"}, {"v": "-12.5e3"}, {"v": "safe"}, {"v": None}, {"v": "한글=값"}]
    rows = [{"i": i, **r} for i, r in enumerate(rows)]  # a one-column row of NULL would be a blank CSV line
    data = to_csv_bytes(tf.build_frame(rows))
    got = [r["v"] for r in csv.DictReader(io.StringIO(data.decode("utf-8-sig")))]
    assert got == ["'=HYPERLINK(\"http://evil\",\"x\")", "'+1+1", "'-2+3", "'@SUM(A1)", "'\tcmd",
                   "-9007199254740993", "-12.5e3", "safe", "", "한글=값"]
    back = pl.read_excel(io.BytesIO(to_xlsx_bytes(tf.build_frame(rows))))
    assert back["v"].to_list()[0].startswith("'=") and back["v"].to_list()[5] == "-9007199254740993"
