"""Engine-agnostic transform pipeline: whitelisted ops only, schema never assumed."""

import datetime as dt
import uuid
from decimal import Decimal

import pytest

from mcpcluster_common import transform as tf

ROWS = [
    {"id": uuid.UUID(int=1), "name": "  Alice ", "email": "alice@acme.example", "amount": Decimal("12.50"), "n": 3, "at": dt.datetime(2026, 9, 1, 10, 0), "tags": ["a", "b"], "nothing": None},
    {"id": uuid.UUID(int=2), "name": "bob", "email": "bob@acme.example", "amount": Decimal("7.25"), "n": None, "at": dt.datetime(2026, 9, 2, 10, 0), "tags": [], "nothing": None},
    {"id": uuid.UUID(int=3), "name": "bob", "email": "bob@acme.example", "amount": Decimal("7.25"), "n": 1, "at": None, "tags": None, "nothing": None},
]


def run(pipeline, rows=ROWS):
    return tf.frame_to_rows(tf.apply(tf.build_frame(rows), tf.parse_pipeline(pipeline)))


def test_build_frame_handles_mixed_and_exotic_types():
    df = tf.build_frame([{"a": 1, "b": "x"}, {"a": "two", "b": None}])  # mixed column -> strings, no failure
    assert df["a"].to_list() == ["1", "two"]
    assert tf.build_frame([]).height == 0


def test_select_rename_filter_sort_limit():
    out = run([
        {"op": "select", "columns": ["name", "amount", "n"]},
        {"op": "trim"},
        {"op": "lower", "columns": ["name"]},
        {"op": "filter", "column": "amount", "cmp": "gt", "value": 7},
        {"op": "rename", "mapping": {"amount": "total"}},
        {"op": "sort", "by": ["total"], "descending": True},
        {"op": "limit", "n": 2},
    ])
    assert [r["name"] for r in out] == ["alice", "bob"] and out[0]["total"] == "12.50"


def test_dedupe_fill_cast_round_arith_concat():
    out = run([
        {"op": "dedupe", "columns": ["name"]},
        {"op": "fill_null", "value": 0, "columns": ["n"]},
        {"op": "cast", "column": "amount", "to": "float"},
        {"op": "arith", "left": "amount", "right": 2, "fn": "mul", "as": "double"},
        {"op": "round", "column": "double", "decimals": 1},
        {"op": "concat", "columns": ["name", "email"], "sep": " <", "as": "who"},
    ])
    assert len(out) == 2 and out[0]["double"] == 25.0 and out[0]["who"].startswith("  Alice  <")


def test_aggregate_grammar():
    out = run([{"op": "aggregate", "group_by": ["name"], "metrics": {"total": "sum(amount)", "rows": "count(*)", "emails": "count_distinct(email)"}}])
    by = {r["name"]: r for r in out}
    assert by["bob"]["rows"] == 2 and by["bob"]["emails"] == 1 and by["  Alice "]["total"] == "12.50"
    with pytest.raises(tf.TransformRejected):
        run([{"op": "aggregate", "metrics": {"x": "sum(*)"}}])
    with pytest.raises(tf.TransformRejected):
        run([{"op": "aggregate", "metrics": {"x": "read_csv('/etc/passwd')"}}])


def test_mask_and_hash_are_data_agnostic():
    out = run([{"op": "mask", "columns": ["email"]}, {"op": "hash", "columns": ["name"]}])
    assert out[0]["email"] == "[EMAIL]" and len(out[0]["name"]) == 64


def test_to_datetime_and_filters_on_strings():
    rows = [{"d": "2026-09-01T10:00:00", "s": "Hello World"}, {"d": "not a date", "s": "bye"}]
    out = run([{"op": "to_datetime", "column": "d"}, {"op": "filter", "column": "s", "cmp": "starts_with", "value": "Hell"}], rows)
    assert out == [{"d": "2026-09-01T10:00:00", "s": "Hello World"}]
    out = run([{"op": "filter", "column": "s", "cmp": "regex", "value": "^b"}], rows)
    assert [r["s"] for r in out] == ["bye"]


@pytest.mark.parametrize("bad", [
    [{"op": "eval", "code": "1+1"}],
    [{"op": "select"}],
    [{"op": "select", "columns": ["name"], "extra": 1}],
    [{"op": "cast", "column": "n", "to": "object"}],
    "not a list",
    [{"op": "limit", "n": 0}],
])
def test_rejects_unknown_or_malformed_ops(bad):
    with pytest.raises(tf.TransformRejected):
        tf.parse_pipeline(bad)


def test_unknown_column_is_reported_with_available_columns():
    with pytest.raises(tf.TransformRejected, match="available"):
        run([{"op": "select", "columns": ["nope"]}])
