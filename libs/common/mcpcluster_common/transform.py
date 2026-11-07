"""Declarative, engine-agnostic data cleaning on normalised rows.

A pipeline is a list of whitelisted operations validated by pydantic (discriminated on `op`). There is
no expression language and no code path that evaluates user strings — every operation maps to a fixed
polars call. Works on whatever rows a datasource returned; the frame is built from arrow-friendly
scalars (numbers, Decimal, datetimes, strings) and degrades gracefully to strings when inference fails.
"""

import re
from typing import Annotated, Any, Literal

import polars as pl
import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from mcpcluster_common import postprocess
from mcpcluster_common.serialize import to_arrow_scalar, to_jsonable

MAX_OPS = 50
METRIC = re.compile(r"^(count|count_distinct|sum|avg|min|max|median)\((\*|[^()]{1,128})\)$")


class TransformRejected(ValueError):
    pass


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Select(_Op):
    op: Literal["select"]
    columns: list[str] = Field(min_length=1)


class Drop(_Op):
    op: Literal["drop"]
    columns: list[str] = Field(min_length=1)


class Rename(_Op):
    op: Literal["rename"]
    mapping: dict[str, str] = Field(min_length=1)


class Cast(_Op):
    op: Literal["cast"]
    column: str
    to: Literal["int", "float", "str", "bool", "date", "datetime"]


class Filter(_Op):
    op: Literal["filter"]
    column: str
    cmp: Literal["eq", "ne", "gt", "ge", "lt", "le", "in", "not_in", "is_null", "not_null", "contains", "starts_with", "ends_with", "regex"]
    value: Any = None


class Dedupe(_Op):
    op: Literal["dedupe"]
    columns: list[str] | None = None
    keep: Literal["first", "last"] = "first"


class FillNull(_Op):
    op: Literal["fill_null"]
    value: Any
    columns: list[str] | None = None


class DropNulls(_Op):
    op: Literal["drop_nulls"]
    columns: list[str] | None = None


class StrOp(_Op):
    op: Literal["trim", "lower", "upper"]
    columns: list[str] | None = None  # None => every string column


class Replace(_Op):
    op: Literal["replace"]
    column: str
    pattern: str = Field(max_length=500)
    replacement: str = Field(max_length=500)
    literal: bool = True


class ToDatetime(_Op):
    op: Literal["to_datetime"]
    column: str
    format: str | None = Field(default=None, max_length=100)  # strftime; None => infer ISO-8601


class Sort(_Op):
    op: Literal["sort"]
    by: list[str] = Field(min_length=1)
    descending: bool | list[bool] = False


class Limit(_Op):
    op: Literal["limit"]
    n: int = Field(ge=1, le=1_000_000)


class Aggregate(_Op):
    op: Literal["aggregate"]
    group_by: list[str] = []
    metrics: dict[str, str] = Field(min_length=1)  # name -> "sum(col)" | "count(*)" | "count_distinct(col)" | ...


class Concat(_Op):
    op: Literal["concat"]
    columns: list[str] = Field(min_length=2)
    sep: str = " "
    as_: str = Field(alias="as")


class Arith(_Op):
    op: Literal["arith"]
    left: str
    right: str | int | float  # column name or literal
    fn: Literal["add", "sub", "mul", "div"]
    as_: str = Field(alias="as")


class Round(_Op):
    op: Literal["round"]
    column: str
    decimals: int = Field(ge=0, le=12)


class Mask(_Op):
    op: Literal["mask"]  # PII redaction on chosen columns (all string columns when omitted)
    columns: list[str] | None = None


class Hash(_Op):
    op: Literal["hash"]  # pseudonymise: sha256 hex of the cell text
    columns: list[str] = Field(min_length=1)


Op = Annotated[
    Select | Drop | Rename | Cast | Filter | Dedupe | FillNull | DropNulls | StrOp | Replace | ToDatetime | Sort | Limit | Aggregate | Concat | Arith | Round | Mask | Hash,
    Field(discriminator="op"),
]
_PIPELINE = TypeAdapter(list[Op])

_CAST = {"int": pl.Int64, "float": pl.Float64, "str": pl.Utf8, "bool": pl.Boolean, "date": pl.Date, "datetime": pl.Datetime("us")}


def parse_pipeline(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TransformRejected("pipeline must be a list of operations")
    if len(raw) > MAX_OPS:
        raise TransformRejected(f"at most {MAX_OPS} operations")
    try:
        return _PIPELINE.validate_python(raw)
    except Exception as e:  # noqa: BLE001 - pydantic error text is the useful part
        raise TransformRejected(f"invalid pipeline: {e}") from e


def build_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """Rows -> DataFrame without assuming a schema. Tries typed inference first, then stringifies
    the offending values; a column with mixed types ends up as strings rather than failing."""
    if not rows:
        return pl.DataFrame()
    prepared = [{k: to_arrow_scalar(v) for k, v in r.items()} for r in rows]
    try:
        return pl.from_arrow(pa.Table.from_pylist(prepared))
    except Exception:  # noqa: BLE001 - mixed types / unsupported inference
        pass
    cols = {k for r in prepared for k in r}
    stringy = [{k: (None if r.get(k) is None else (r[k] if isinstance(r.get(k), (int, float, bool)) else str(r[k]))) for k in cols} for r in prepared]
    try:
        return pl.from_arrow(pa.Table.from_pylist(stringy))
    except Exception:  # noqa: BLE001
        return pl.DataFrame({k: [None if r.get(k) is None else str(r[k]) for r in prepared] for k in cols})


def _need(df: pl.DataFrame, *cols: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise TransformRejected(f"unknown column(s): {missing}; available: {df.columns}")


def _str_cols(df: pl.DataFrame, cols: list[str] | None) -> list[str]:
    if cols is None:
        return [c for c, t in zip(df.columns, df.dtypes, strict=True) if t == pl.Utf8]
    _need(df, *cols)
    return cols


def _metric_expr(name: str, spec: str) -> pl.Expr:
    m = METRIC.match(spec.replace(" ", ""))
    if not m:
        raise TransformRejected(f"metric '{spec}' must look like sum(col) / count(*) / count_distinct(col) / avg|min|max|median(col)")
    fn, col = m.groups()
    if col == "*":
        if fn != "count":
            raise TransformRejected("only count(*) may use *")
        return pl.len().alias(name)
    c = pl.col(col)
    expr = {"count": c.count(), "count_distinct": c.n_unique(), "sum": c.sum(), "avg": c.mean(), "min": c.min(), "max": c.max(), "median": c.median()}[fn]
    return expr.alias(name)


def apply(df: pl.DataFrame, pipeline: list[Any]) -> pl.DataFrame:  # noqa: PLR0912, PLR0915 - one branch per whitelisted op
    for op in pipeline:
        if isinstance(op, Select):
            _need(df, *op.columns)
            df = df.select(op.columns)
        elif isinstance(op, Drop):
            _need(df, *op.columns)
            df = df.drop(op.columns)
        elif isinstance(op, Rename):
            _need(df, *op.mapping)
            df = df.rename(op.mapping)
        elif isinstance(op, Cast):
            _need(df, op.column)
            df = df.with_columns(pl.col(op.column).cast(_CAST[op.to], strict=False))
        elif isinstance(op, Filter):
            _need(df, op.column)
            c = pl.col(op.column)
            v = op.value
            if op.cmp in ("is_null", "not_null"):
                cond = c.is_null() if op.cmp == "is_null" else c.is_not_null()
            elif op.cmp in ("in", "not_in"):
                if not isinstance(v, list):
                    raise TransformRejected("in/not_in need a list value")
                cond = c.is_in(v) if op.cmp == "in" else ~c.is_in(v)
            elif op.cmp in ("contains", "starts_with", "ends_with", "regex"):
                sv = str(v)
                cs = c.cast(pl.Utf8)
                cond = {"contains": cs.str.contains(sv, literal=True), "starts_with": cs.str.starts_with(sv), "ends_with": cs.str.ends_with(sv), "regex": cs.str.contains(sv)}[op.cmp]
            else:
                cond = {"eq": c == v, "ne": c != v, "gt": c > v, "ge": c >= v, "lt": c < v, "le": c <= v}[op.cmp]
            df = df.filter(cond)
        elif isinstance(op, Dedupe):
            if op.columns:
                _need(df, *op.columns)
            df = df.unique(subset=op.columns, keep=op.keep, maintain_order=True)
        elif isinstance(op, FillNull):
            cols = op.columns or df.columns
            _need(df, *cols)
            df = df.with_columns([pl.col(c).fill_null(op.value) for c in cols])
        elif isinstance(op, DropNulls):
            if op.columns:
                _need(df, *op.columns)
            df = df.drop_nulls(subset=op.columns)
        elif isinstance(op, StrOp):
            cols = _str_cols(df, op.columns)
            f = {"trim": lambda e: e.str.strip_chars(), "lower": lambda e: e.str.to_lowercase(), "upper": lambda e: e.str.to_uppercase()}[op.op]
            df = df.with_columns([f(pl.col(c).cast(pl.Utf8)) for c in cols])
        elif isinstance(op, Replace):
            _need(df, op.column)
            df = df.with_columns(pl.col(op.column).cast(pl.Utf8).str.replace_all(op.pattern, op.replacement, literal=op.literal))
        elif isinstance(op, ToDatetime):
            _need(df, op.column)
            df = df.with_columns(pl.col(op.column).cast(pl.Utf8).str.to_datetime(op.format, strict=False))
        elif isinstance(op, Sort):
            _need(df, *op.by)
            df = df.sort(op.by, descending=op.descending, nulls_last=True)
        elif isinstance(op, Limit):
            df = df.head(op.n)
        elif isinstance(op, Aggregate):
            if op.group_by:
                _need(df, *op.group_by)
            exprs = [_metric_expr(n, s) for n, s in op.metrics.items()]
            df = df.group_by(op.group_by, maintain_order=True).agg(exprs) if op.group_by else df.select(exprs)
        elif isinstance(op, Concat):
            _need(df, *op.columns)
            df = df.with_columns(pl.concat_str([pl.col(c).cast(pl.Utf8) for c in op.columns], separator=op.sep, ignore_nulls=True).alias(op.as_))
        elif isinstance(op, Arith):
            _need(df, op.left)
            right = pl.col(op.right) if isinstance(op.right, str) else pl.lit(op.right)
            if isinstance(op.right, str):
                _need(df, op.right)
            left = pl.col(op.left)
            expr = {"add": left + right, "sub": left - right, "mul": left * right, "div": left / right}[op.fn]
            df = df.with_columns(expr.alias(op.as_))
        elif isinstance(op, Round):
            _need(df, op.column)
            df = df.with_columns(pl.col(op.column).cast(pl.Float64).round(op.decimals))
        elif isinstance(op, Mask):
            df = redact_frame(df, _str_cols(df, op.columns), force=True)
        elif isinstance(op, Hash):
            _need(df, *op.columns)
            for c in op.columns:
                vals = df[c].cast(pl.Utf8).to_list()
                hashed = postprocess.sha256_hex_batch(["" if v is None else v for v in vals])
                df = df.with_columns(pl.Series(c, [None if v is None else h for v, h in zip(vals, hashed, strict=True)], dtype=pl.Utf8))
    return df


def redact_frame(df: pl.DataFrame, columns: list[str] | None = None, *, force: bool = False) -> pl.DataFrame:
    """PII redaction over string columns in one batched (Rust) call per column."""
    if not (force or postprocess.ENABLED) or df.height == 0:
        return df
    cols = columns if columns is not None else [c for c, t in zip(df.columns, df.dtypes, strict=True) if t == pl.Utf8]
    for c in cols:
        df = df.with_columns(pl.Series(c, postprocess.redact_batch(df[c].to_list()), dtype=pl.Utf8))
    return df


def frame_to_rows(df: pl.DataFrame) -> list[dict[str, Any]]:
    return [{k: to_jsonable(v) for k, v in r.items()} for r in df.iter_rows(named=True)]
