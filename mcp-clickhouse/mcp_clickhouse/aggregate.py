"""Aggregation over a tenant-scoped base query: rows are pulled from ClickHouse as Arrow and
aggregated with DuckDB (large groupings) — never with hand-built SQL against ClickHouse."""

import re

import duckdb
import polars as pl
import pyarrow as pa

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
METRIC = re.compile(r"^(count|count_distinct|sum|avg|min|max|median)\((\*|[A-Za-z_][A-Za-z0-9_]{0,127})\)$")

DUCK_FUNC = {"count": "count", "count_distinct": "count(DISTINCT {})", "sum": "sum", "avg": "avg", "min": "min", "max": "max", "median": "median"}


class AggregateRejected(ValueError):
    pass


def build_duck_sql(group_by: list[str], metrics: dict[str, str], limit: int) -> str:
    for g in group_by:
        if not IDENT.match(g):
            raise AggregateRejected(f"bad group_by identifier: {g}")
    if not metrics:
        raise AggregateRejected("at least one metric is required")
    selects = [f'"{g}"' for g in group_by]
    for name, expr in metrics.items():
        if not IDENT.match(name):
            raise AggregateRejected(f"bad metric name: {name}")
        m = METRIC.match(expr.replace(" ", ""))
        if not m:
            raise AggregateRejected(f"metric '{expr}' must look like sum(col) / count(*) / count_distinct(col) / …")
        fn, col = m.groups()
        if col == "*" and fn != "count":
            raise AggregateRejected("only count(*) may use *")
        col_sql = "*" if col == "*" else f'"{col}"'
        piece = DUCK_FUNC[fn].format(col_sql) if "{}" in DUCK_FUNC[fn] else f"{DUCK_FUNC[fn]}({col_sql})"
        selects.append(f'{piece} AS "{name}"')
    sql = f"SELECT {', '.join(selects)} FROM base"
    if group_by:
        sql += " GROUP BY " + ", ".join(f'"{g}"' for g in group_by) + " ORDER BY " + ", ".join(f'"{g}"' for g in group_by)
    return sql + f" LIMIT {int(limit)}"


def run(base: pa.Table, group_by: list[str], metrics: dict[str, str], limit: int = 1000) -> pl.DataFrame:
    sql = build_duck_sql(group_by, metrics, limit)
    con = duckdb.connect(database=":memory:")
    try:
        con.register("base", base)
        return pl.from_arrow(con.execute(sql).fetch_arrow_table())
    finally:
        con.close()
