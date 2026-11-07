"""Validated `compute` op specs and the glue from a polars frame to the Rust kernels.

Two hand-off modes, chosen per call:
  * copy  — columns become Python lists (one object per cell); cheapest for small inputs
  * arrow — columns are exported by polars as pyarrow arrays and read by Rust through the Arrow C Data
            Interface, no per-cell Python objects; wins from a few tens of thousands of rows
`transfer="auto"` picks arrow at/above `compute_arrow_threshold_rows` when the wheel supports it, and
falls back to copy if a column cannot be expressed as a supported arrow type."""

import logging
from typing import Annotated, Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from mcpcluster_common import compute as cp

log = logging.getLogger(__name__)
Transfer = Literal["auto", "copy", "arrow"]

BUCKETS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "1d": 86400, "1w": 604800}


class ComputeRejected(ValueError):
    pass


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Describe(_Spec):
    op: Literal["describe"]
    column: str


class Percentiles(_Spec):
    op: Literal["percentiles"]
    column: str
    ps: list[float] = Field(min_length=1, max_length=100)


class Histogram(_Spec):
    op: Literal["histogram"]
    column: str
    bins: int = Field(default=20, ge=1, le=10_000)


class GroupStats(_Spec):
    op: Literal["group_stats"]
    key: str
    value: str
    limit: int = Field(default=1000, ge=1, le=100_000)


class TopK(_Spec):
    op: Literal["top_k"]
    column: str
    k: int = Field(default=20, ge=1, le=10_000)


class DistinctCount(_Spec):
    op: Literal["distinct_count"]
    column: str


class Correlation(_Spec):
    op: Literal["correlation"]
    x: str
    y: str


class Outliers(_Spec):
    op: Literal["outliers"]
    column: str
    method: Literal["iqr", "zscore"] = "iqr"
    threshold: float = Field(default=1.5, gt=0)
    max_indices: int = Field(default=1000, ge=0, le=100_000)


class TimeBucket(_Spec):
    op: Literal["time_bucket"]
    time: str
    value: str | None = None  # None with agg=count
    bucket: str = "1h"  # 1m 5m 15m 1h 6h 1d 1w or "<seconds>s"
    agg: Literal["count", "sum", "mean", "min", "max"] = "count"


class MovingAverage(_Spec):
    op: Literal["moving_average"]
    column: str
    window: int = Field(ge=1, le=100_000)
    order_by: str | None = None
    limit: int = Field(default=10_000, ge=1, le=100_000)


class LinearTrend(_Spec):
    op: Literal["linear_trend"]
    y: str
    x: str | None = None  # None: row order 0..n-1; datetime columns become epoch seconds
    horizon: int = Field(default=0, ge=0, le=1000)


class Growth(_Spec):
    op: Literal["growth"]
    column: str
    order_by: str | None = None
    lag: int = Field(default=1, ge=1, le=100_000)
    limit: int = Field(default=10_000, ge=1, le=100_000)


class Pareto(_Spec):
    op: Literal["pareto"]
    key: str
    value: str
    threshold: float = Field(default=0.8, gt=0, le=1)
    limit: int = Field(default=1000, ge=1, le=100_000)


class ABTest(_Spec):
    op: Literal["ab_test"]
    group: str
    value: str
    a: str
    b: str


class Seasonality(_Spec):
    op: Literal["seasonality"]
    time: str
    value: str | None = None  # None: count rows per bucket
    period: Literal["month", "weekday", "quarter", "hour"]


Spec = Annotated[Describe | Percentiles | Histogram | GroupStats | TopK | DistinctCount | Correlation | Outliers | TimeBucket | MovingAverage
                 | LinearTrend | Growth | Pareto | ABTest | Seasonality, Field(discriminator="op")]
_SPEC = TypeAdapter(Spec)


def parse_compute(raw: Any) -> Any:
    try:
        return _SPEC.validate_python(raw)
    except Exception as e:  # noqa: BLE001
        raise ComputeRejected(f"invalid compute op: {e}") from e


def _col(df: pl.DataFrame, name: str) -> pl.Series:
    if name not in df.columns:
        raise ComputeRejected(f"unknown column {name!r}; available: {df.columns}")
    return df[name]


def _nums(s: pl.Series) -> list[float | None]:
    """Any column -> floats or None (strings that parse as numbers count; everything else is null)."""
    try:
        return s.cast(pl.Float64, strict=False).to_list()
    except Exception:  # noqa: BLE001
        return s.cast(pl.Utf8).cast(pl.Float64, strict=False).to_list()


def _keys(s: pl.Series) -> list[str | None]:
    return s.cast(pl.Utf8).to_list()


def _epoch(s: pl.Series) -> list[int | None]:
    if s.dtype == pl.Datetime or s.dtype == pl.Date:
        return s.cast(pl.Datetime("us")).dt.epoch("s").to_list()
    if s.dtype.is_numeric():
        return s.cast(pl.Int64, strict=False).to_list()
    return s.cast(pl.Utf8).str.to_datetime(strict=False).dt.epoch("s").to_list()


def _xnums(s: pl.Series) -> list[float | None]:
    """Trend x axis: datetime/date columns as epoch seconds, anything else as numbers."""
    if s.dtype == pl.Datetime or s.dtype == pl.Date:
        return [float(v) if v is not None else None for v in _epoch(s)]
    return _nums(s)


def _bucket_secs(b: str) -> int:
    if b in BUCKETS:
        return BUCKETS[b]
    if b.endswith("s") and b[:-1].isdigit():
        return max(int(b[:-1]), 1)
    raise ComputeRejected(f"bucket must be one of {sorted(BUCKETS)} or '<seconds>s'")


# ---- arrow-side column adapters: polars -> pyarrow without copying the buffers ----------------

def _nums_arrow(s: pl.Series):
    try:
        return s.cast(pl.Float64, strict=False).to_arrow()
    except Exception:  # noqa: BLE001
        return s.cast(pl.Utf8).cast(pl.Float64, strict=False).to_arrow()


def _keys_arrow(s: pl.Series):
    return s.cast(pl.Utf8).to_arrow()


def _epoch_arrow(s: pl.Series):
    if s.dtype == pl.Datetime or s.dtype == pl.Date:
        return s.cast(pl.Datetime("us")).dt.epoch("s").cast(pl.Int64).to_arrow()
    if s.dtype.is_numeric():
        return s.cast(pl.Int64, strict=False).to_arrow()
    return s.cast(pl.Utf8).str.to_datetime(strict=False).dt.epoch("s").cast(pl.Int64).to_arrow()


def _xnums_arrow(s: pl.Series):
    if s.dtype == pl.Datetime or s.dtype == pl.Date:
        return _epoch_arrow(s)
    return _nums_arrow(s)


def choose_transfer(requested: Transfer, rows: int, threshold: int) -> str:
    if requested == "copy" or not cp.arrow_available():
        return "copy"
    if requested == "arrow":
        return "arrow"
    return "arrow" if rows >= threshold else "copy"


def run_compute(df: pl.DataFrame, spec: Any, *, transfer: Transfer = "auto", arrow_threshold: int = 20_000) -> tuple[Any, str]:
    """Returns (result, transfer_used). The arrow path falls back to copy on any conversion problem."""
    mode = choose_transfer(transfer, df.height, arrow_threshold)
    if mode == "arrow":
        try:
            return _run_arrow(df, spec), "arrow"
        except (ComputeRejected, RuntimeError):
            raise
        except Exception as e:  # noqa: BLE001 - unsupported arrow type etc.
            log.info("arrow transfer failed (%s); falling back to copy", e)
    return _run_copy(df, spec), "copy"


def _finish_time_bucket(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import datetime as dt

    for r in rows:
        r["bucket"] = dt.datetime.fromtimestamp(r["bucket_epoch"], tz=dt.UTC).isoformat()
    return rows


def _run_copy(df: pl.DataFrame, spec: Any) -> Any:
    if isinstance(spec, Describe):
        return cp.describe(_nums(_col(df, spec.column)))
    if isinstance(spec, Percentiles):
        return dict(zip([str(p) for p in spec.ps], cp.percentiles(_nums(_col(df, spec.column)), spec.ps), strict=True))
    if isinstance(spec, Histogram):
        return cp.histogram(_nums(_col(df, spec.column)), spec.bins)
    if isinstance(spec, GroupStats):
        return cp.group_stats(_keys(_col(df, spec.key)), _nums(_col(df, spec.value)))[: spec.limit]
    if isinstance(spec, TopK):
        return cp.top_k(_keys(_col(df, spec.column)), spec.k)
    if isinstance(spec, DistinctCount):
        return {"distinct": cp.distinct_count(_keys(_col(df, spec.column))), "rows": df.height}
    if isinstance(spec, Correlation):
        return cp.correlation(_nums(_col(df, spec.x)), _nums(_col(df, spec.y)))
    if isinstance(spec, Outliers):
        r = cp.outliers(_nums(_col(df, spec.column)), spec.method, spec.threshold)
        r["indices"] = r["indices"][: spec.max_indices]
        return r
    if isinstance(spec, TimeBucket):
        ts = _epoch(_col(df, spec.time))
        vals = _nums(_col(df, spec.value)) if spec.value else [0.0] * df.height
        return _finish_time_bucket(cp.time_bucket(ts, vals, _bucket_secs(spec.bucket), spec.agg))
    if isinstance(spec, MovingAverage):
        if spec.order_by:
            df = df.sort(_col(df, spec.order_by).name, nulls_last=True)
        return {"values": cp.moving_average(_nums(_col(df, spec.column)), spec.window)[: spec.limit], "window": spec.window}
    if isinstance(spec, LinearTrend):
        x = _xnums(_col(df, spec.x)) if spec.x else None
        return cp.linear_trend(_nums(_col(df, spec.y)), x, spec.horizon)
    if isinstance(spec, Growth):
        if spec.order_by:
            df = df.sort(_col(df, spec.order_by).name, nulls_last=True)
        r = cp.growth(_nums(_col(df, spec.column)), spec.lag)
        r["rows"] = r["rows"][: spec.limit]
        return {**r, "lag": spec.lag}
    if isinstance(spec, Pareto):
        r = cp.pareto(_keys(_col(df, spec.key)), _nums(_col(df, spec.value)), spec.threshold)
        r["rows"] = r["rows"][: spec.limit]
        return {**r, "threshold": spec.threshold}
    if isinstance(spec, ABTest):
        return cp.ab_test(_keys(_col(df, spec.group)), _nums(_col(df, spec.value)), spec.a, spec.b)
    if isinstance(spec, Seasonality):
        vals = _nums(_col(df, spec.value)) if spec.value else None
        return cp.seasonality(_epoch(_col(df, spec.time)), vals, spec.period)
    raise ComputeRejected("unsupported op")


def _run_arrow(df: pl.DataFrame, spec: Any) -> Any:
    a = cp.arrow
    if isinstance(spec, Describe):
        return a.describe(_nums_arrow(_col(df, spec.column)))
    if isinstance(spec, Percentiles):
        return dict(zip([str(p) for p in spec.ps], a.percentiles(_nums_arrow(_col(df, spec.column)), spec.ps), strict=True))
    if isinstance(spec, Histogram):
        return a.histogram(_nums_arrow(_col(df, spec.column)), spec.bins)
    if isinstance(spec, GroupStats):
        return a.group_stats(_keys_arrow(_col(df, spec.key)), _nums_arrow(_col(df, spec.value)))[: spec.limit]
    if isinstance(spec, TopK):
        return a.top_k(_keys_arrow(_col(df, spec.column)), spec.k)
    if isinstance(spec, DistinctCount):
        return {"distinct": a.distinct_count(_keys_arrow(_col(df, spec.column))), "rows": df.height}
    if isinstance(spec, Correlation):
        return a.correlation(_nums_arrow(_col(df, spec.x)), _nums_arrow(_col(df, spec.y)))
    if isinstance(spec, Outliers):
        r = a.outliers(_nums_arrow(_col(df, spec.column)), spec.method, spec.threshold)
        r["indices"] = r["indices"][: spec.max_indices]
        return r
    if isinstance(spec, TimeBucket):
        ts = _epoch_arrow(_col(df, spec.time))
        vals = _nums_arrow(_col(df, spec.value)) if spec.value else pl.Series([0.0] * df.height, dtype=pl.Float64).to_arrow()
        return _finish_time_bucket(a.time_bucket(ts, vals, _bucket_secs(spec.bucket), spec.agg))
    if isinstance(spec, MovingAverage):
        if spec.order_by:
            df = df.sort(_col(df, spec.order_by).name, nulls_last=True)
        return {"values": a.moving_average(_nums_arrow(_col(df, spec.column)), spec.window)[: spec.limit], "window": spec.window}
    if isinstance(spec, LinearTrend):
        x = _xnums_arrow(_col(df, spec.x)) if spec.x else None
        return a.linear_trend(_nums_arrow(_col(df, spec.y)), x, spec.horizon)
    if isinstance(spec, Growth):
        if spec.order_by:
            df = df.sort(_col(df, spec.order_by).name, nulls_last=True)
        r = a.growth(_nums_arrow(_col(df, spec.column)), spec.lag)
        r["rows"] = r["rows"][: spec.limit]
        return {**r, "lag": spec.lag}
    if isinstance(spec, Pareto):
        r = a.pareto(_keys_arrow(_col(df, spec.key)), _nums_arrow(_col(df, spec.value)), spec.threshold)
        r["rows"] = r["rows"][: spec.limit]
        return {**r, "threshold": spec.threshold}
    if isinstance(spec, ABTest):
        return a.ab_test(_keys_arrow(_col(df, spec.group)), _nums_arrow(_col(df, spec.value)), spec.a, spec.b)
    if isinstance(spec, Seasonality):
        vals = _nums_arrow(_col(df, spec.value)) if spec.value else None
        return a.seasonality(_epoch_arrow(_col(df, spec.time)), vals, spec.period)
    raise ComputeRejected("unsupported op")
