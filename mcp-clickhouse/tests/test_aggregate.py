import polars as pl
import pyarrow as pa
import pytest

from mcp_clickhouse.aggregate import AggregateRejected, build_duck_sql, run


def test_aggregate_runs_with_duckdb():
    base = pa.table({"kind": ["a", "a", "b"], "value": [1.0, 2.0, 5.0], "uid": [1, 1, 2]})
    df = run(base, ["kind"], {"total": "sum(value)", "n": "count(*)", "users": "count_distinct(uid)"})
    assert df.sort("kind").to_dicts() == [{"kind": "a", "total": 3.0, "n": 2, "users": 1}, {"kind": "b", "total": 5.0, "n": 1, "users": 1}]


def test_no_group_by():
    base = pa.table({"value": [1.0, 2.0]})
    assert run(base, [], {"m": "max(value)"}).to_dicts() == [{"m": 2.0}]


@pytest.mark.parametrize("gb,metrics", [
    (["kind; drop"], {"n": "count(*)"}),
    (["kind"], {"n": "count(*) --"}),
    (["kind"], {"bad name": "count(*)"}),
    (["kind"], {"n": "sum(*)"}),
    (["kind"], {}),
    (["kind"], {"n": "read_csv('/etc/passwd')"}),
])
def test_rejects(gb, metrics):
    with pytest.raises(AggregateRejected):
        build_duck_sql(gb, metrics, 10)


def test_polars_roundtrip():
    assert isinstance(run(pa.table({"v": [1]}), [], {"n": "count(*)"}), pl.DataFrame)
