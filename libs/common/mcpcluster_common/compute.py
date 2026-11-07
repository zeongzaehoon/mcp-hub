"""Numeric/statistical kernels: Rust (`mcpcluster_core.compute*`, rayon) when the wheel is present,
pure-Python fallback with identical semantics otherwise. Tests assert parity per function.

Conventions (both implementations): percentiles use linear interpolation (numpy default / R type 7);
std is the sample standard deviation (n-1, None when n < 2); non-finite values count as nulls;
grouped outputs are ordered by count desc then key asc; null keys are the string "<null>"."""

import math
from collections import defaultdict
from typing import Any

try:
    import mcpcluster_core as _core

    USING_RUST = hasattr(_core, "describe")
    ARROW_SUPPORT = bool(getattr(_core, "ARROW_SUPPORT", False))
except ImportError:  # pragma: no cover
    _core = None
    USING_RUST = False
    ARROW_SUPPORT = False

NULL_KEY = "<null>"


def _clean(values: list[float | None]) -> list[float]:
    return [float(v) for v in values if v is not None and math.isfinite(float(v))]


def _pct(s: list[float], p: float) -> float | None:
    if not s:
        return None
    p = min(max(p, 0.0), 100.0)
    rank = (p / 100.0) * (len(s) - 1)
    lo, hi = math.floor(rank), math.ceil(rank)
    return s[lo] + (s[hi] - s[lo]) * (rank - lo)


def _mean_std(v: list[float]) -> tuple[float | None, float | None]:
    n = len(v)
    if n == 0:
        return None, None
    mean = sum(v) / n
    if n < 2:
        return mean, None
    return mean, math.sqrt(sum((x - mean) ** 2 for x in v) / (n - 1))


def describe(values: list[float | None], *, force_python: bool = False) -> dict[str, Any]:
    if USING_RUST and not force_python:
        return dict(_core.describe(values))
    nulls = sum(1 for v in values if v is None or not math.isfinite(float(v)))
    s = sorted(_clean(values))
    mean, std = _mean_std(s)
    return {"count": len(s), "nulls": nulls, "mean": mean, "std": std, "min": s[0] if s else None, "max": s[-1] if s else None,
            "sum": sum(s) if s else None, "p25": _pct(s, 25), "p50": _pct(s, 50), "p75": _pct(s, 75), "p95": _pct(s, 95), "p99": _pct(s, 99)}


def percentiles(values: list[float | None], ps: list[float], *, force_python: bool = False) -> list[float | None]:
    if USING_RUST and not force_python:
        return list(_core.percentiles(values, [float(p) for p in ps]))
    s = sorted(_clean(values))
    return [_pct(s, float(p)) for p in ps]


def histogram(values: list[float | None], bins: int, *, force_python: bool = False) -> dict[str, Any]:
    bins = min(max(int(bins), 1), 10_000)
    if USING_RUST and not force_python:
        return dict(_core.histogram(values, bins))
    v = _clean(values)
    if not v:
        return {"edges": [], "counts": []}
    mn, mx = min(v), max(v)
    width = (mx - mn) / bins if mx > mn else 1.0
    edges = [mn + width * i for i in range(bins + 1)]
    counts = [0] * bins
    for x in v:
        i = min(int(math.floor((x - mn) / width)), bins - 1)
        counts[i] += 1
    return {"edges": edges, "counts": counts}


def group_stats(keys: list[str | None], values: list[float | None], *, force_python: bool = False) -> list[dict[str, Any]]:
    if USING_RUST and not force_python:
        return [dict(d) for d in _core.group_stats(keys, values)]
    acc: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "nulls": 0, "sum": 0.0, "min": math.inf, "max": -math.inf})
    for k, v in zip(keys, values, strict=True):
        e = acc[NULL_KEY if k is None else k]
        if v is not None and math.isfinite(float(v)):
            x = float(v)
            e["count"] += 1
            e["sum"] += x
            e["min"] = min(e["min"], x)
            e["max"] = max(e["max"], x)
        else:
            e["nulls"] += 1
    rows = sorted(acc.items(), key=lambda kv: (-(kv[1]["count"] + kv[1]["nulls"]), kv[0]))
    return [{"key": k, "count": a["count"], "nulls": a["nulls"], "sum": a["sum"] if a["count"] else None,
             "mean": a["sum"] / a["count"] if a["count"] else None, "min": a["min"] if a["count"] else None, "max": a["max"] if a["count"] else None} for k, a in rows]


def top_k(keys: list[str | None], k: int, *, force_python: bool = False) -> list[dict[str, Any]]:
    if USING_RUST and not force_python:
        return [dict(d) for d in _core.top_k(keys, int(k))]
    counts: dict[str, int] = defaultdict(int)
    for key in keys:
        counts[NULL_KEY if key is None else key] += 1
    rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[: max(int(k), 1)]
    return [{"key": key, "count": c} for key, c in rows]


def distinct_count(keys: list[str | None], *, force_python: bool = False) -> int:
    if USING_RUST and not force_python:
        return int(_core.distinct_count(keys))
    return len(set(keys))


def _ranks(v: list[float]) -> list[float]:
    idx = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and v[idx[j + 1]] == v[idx[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[idx[k]] = avg
        i = j + 1
    return r


def _pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 2:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def correlation(x: list[float | None], y: list[float | None], *, force_python: bool = False) -> dict[str, Any]:
    if USING_RUST and not force_python:
        return dict(_core.correlation(x, y))
    pairs = [(float(a), float(b)) for a, b in zip(x, y, strict=True) if a is not None and b is not None and math.isfinite(float(a)) and math.isfinite(float(b))]
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    return {"n": len(pairs), "pearson": _pearson(xs, ys), "spearman": _pearson(_ranks(xs), _ranks(ys))}


def outliers(values: list[float | None], method: str = "iqr", threshold: float = 1.5, *, force_python: bool = False) -> dict[str, Any]:
    if USING_RUST and not force_python:
        return dict(_core.outliers(values, method, float(threshold)))
    v = _clean(values)
    if method == "iqr":
        s = sorted(v)
        q1, q3 = _pct(s, 25), _pct(s, 75)
        lo, hi = (q1 - threshold * (q3 - q1), q3 + threshold * (q3 - q1)) if q1 is not None else (-math.inf, math.inf)
    else:
        m, sd = _mean_std(v)
        lo, hi = (m - threshold * sd, m + threshold * sd) if sd is not None else (-math.inf, math.inf)
    idx = [i for i, x in enumerate(values) if x is not None and math.isfinite(float(x)) and (float(x) < lo or float(x) > hi)]
    return {"method": method, "lower": lo if math.isfinite(lo) else None, "upper": hi if math.isfinite(hi) else None, "count": len(idx), "indices": idx}


def time_bucket(ts: list[int | None], values: list[float | None], bucket_secs: int, agg: str = "count", *, force_python: bool = False) -> list[dict[str, Any]]:
    bucket_secs = max(int(bucket_secs), 1)
    if USING_RUST and not force_python:
        return [dict(d) for d in _core.time_bucket(ts, values, bucket_secs, agg)]
    acc: dict[int, dict[str, Any]] = {}
    for t, v in zip(ts, values, strict=True):
        if t is None:
            continue
        b = (int(t) // bucket_secs) * bucket_secs
        e = acc.setdefault(b, {"count": 0, "nulls": 0, "sum": 0.0, "min": math.inf, "max": -math.inf})
        if v is not None and math.isfinite(float(v)):
            x = float(v)
            e["count"] += 1
            e["sum"] += x
            e["min"] = min(e["min"], x)
            e["max"] = max(e["max"], x)
        else:
            e["nulls"] += 1
    out = []
    for b in sorted(acc):
        a = acc[b]
        value = {"count": float(a["count"] + a["nulls"]), "sum": a["sum"] if a["count"] else None, "mean": a["sum"] / a["count"] if a["count"] else None,
                 "min": a["min"] if a["count"] else None, "max": a["max"] if a["count"] else None}.get(agg)
        out.append({"bucket_epoch": b, "count": a["count"] + a["nulls"], "value": value})
    return out


def moving_average(values: list[float | None], window: int, *, force_python: bool = False) -> list[float | None]:
    w = max(int(window), 1)
    if USING_RUST and not force_python:
        return list(_core.moving_average(values, w))
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < w:
            out.append(None)
            continue
        sl = values[i + 1 - w : i + 1]
        if any(v is None or not math.isfinite(float(v)) for v in sl):
            out.append(None)
        else:
            out.append(sum(float(v) for v in sl) / w)
    return out


# ---- business-analysis kernels (sums fold left-to-right, same as the Rust side) -------------


def _fin(v: Any) -> float | None:
    return float(v) if v is not None and math.isfinite(float(v)) else None


def linear_trend(y: list[float | None], x: list[float | None] | None = None, horizon: int = 0, *, force_python: bool = False) -> dict[str, Any]:
    """OLS over rows where both are finite; x None = row index (step 1), else step = mean consecutive x diff."""
    horizon = min(int(horizon), 1000)
    if USING_RUST and not force_python:
        r = dict(_core.linear_trend(y, x, horizon))
        r["forecast"] = [dict(p) for p in r["forecast"]]
        return r
    index_x = x is None
    xs_src = [float(i) for i in range(len(y))] if x is None else x
    pairs = [(a, b) for a, b in ((_fin(a), _fin(b)) for a, b in zip(xs_src, y, strict=True)) if a is not None and b is not None]
    n = len(pairs)
    empty = {"n": n, "slope": None, "intercept": None, "r2": None, "forecast": []}
    if n < 2:
        return empty
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sxy = syy = 0.0
    for a, b in zip(xs, ys, strict=True):
        dx, dy = a - mx, b - my
        sxx += dx * dx
        sxy += dx * dy
        syy += dy * dy
    if sxx == 0.0:
        return empty
    slope = sxy / sxx
    intercept = my - slope * mx
    r2 = None if syy == 0.0 else sxy * sxy / (sxx * syy)
    step = 1.0 if index_x else (xs[-1] - xs[0]) / (n - 1)
    forecast = []
    for i in range(1, horizon + 1):
        fx = xs[-1] + step * i
        forecast.append({"x": fx, "y": slope * fx + intercept})
    return {"n": n, "slope": slope, "intercept": intercept, "r2": r2, "forecast": forecast}


def growth(values: list[float | None], lag: int = 1, *, force_python: bool = False) -> dict[str, Any]:
    lag = max(int(lag), 1)
    if USING_RUST and not force_python:
        r = dict(_core.growth(values, lag))
        r["rows"] = [dict(d) for d in r["rows"]]
        return r
    rows = []
    for i, raw in enumerate(values):
        value = _fin(raw)
        prev = _fin(values[i - lag]) if i >= lag else None
        if value is not None and prev is not None:
            abs_change = value - prev
            pct_change = None if prev == 0.0 else (value - prev) / abs(prev) * 100.0
        else:
            abs_change = pct_change = None
        rows.append({"index": i, "value": value, "prev": prev, "abs_change": abs_change, "pct_change": pct_change})
    finite_vals = [v for v in (_fin(v) for v in values) if v is not None]
    first = finite_vals[0] if finite_vals else None
    last = finite_vals[-1] if finite_vals else None
    total = (last - first) / abs(first) * 100.0 if first is not None and last is not None and first != 0.0 else None
    return {"rows": rows, "first": first, "last": last, "total_pct_change": total}


def pareto(keys: list[str | None], values: list[float | None], threshold: float = 0.8, *, force_python: bool = False) -> dict[str, Any]:
    if USING_RUST and not force_python:
        r = dict(_core.pareto(keys, values, float(threshold)))
        r["rows"] = [dict(d) for d in r["rows"]]
        return r
    sums: dict[str, float] = {}
    for k, raw in zip(keys, values, strict=True):
        v = _fin(raw)
        if v is not None:
            key = NULL_KEY if k is None else k
            sums[key] = sums.get(key, 0.0) + v
    ordered = sorted(sums.items(), key=lambda kv: (-kv[1], kv[0]))
    total = sum(s for _, s in ordered)
    rows, cum, k_to = [], 0.0, None
    for i, (k, s) in enumerate(ordered):
        if total > 0.0:
            cum += s / total
            share, cum_share = s / total, cum
        else:
            share = cum_share = None
        if k_to is None and total > 0.0 and cum >= threshold:
            k_to = i + 1
        rows.append({"key": k, "sum": s, "share": share, "cum_share": cum_share})
    if k_to is None and total > 0.0:
        k_to = len(rows)  # rounding kept the last cum_share a hair below 1.0
    return {"total": total, "rows": rows, "keys_to_threshold": k_to}


# Lanczos ln(Gamma) (Numerical Recipes 3rd ed. gammln) and regularized incomplete beta via the modified
# Lentz continued fraction — identical constants and iteration order to compute.rs so p-values agree.
_GAMMLN_COF = (57.1562356658629235, -59.5979603554754912, 14.1360979747417471, -0.491913816097620199,
               0.339946499848118887e-4, 0.465236289270485756e-4, -0.983744753048795646e-4, 0.158088703224912494e-3,
               -0.210264441724104883e-3, 0.217439618115212643e-3, -0.164318106536763890e-3, 0.844182239838527433e-4,
               -0.261908384015814087e-4, 0.368991826595316234e-5)


def _gammln(xx: float) -> float:
    x = y = xx
    tmp = x + 5.24218750000000000
    tmp = (x + 0.5) * math.log(tmp) - tmp
    ser = 0.999999999999997092
    for c in _GAMMLN_COF:
        y += 1.0
        ser += c / y
    return tmp + math.log(2.5066282746310005 * ser / x)


def _betacf(a: float, b: float, x: float) -> float:
    maxit, eps, fpmin = 300, 1e-14, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2.0 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(_gammln(a + b) - _gammln(a) - _gammln(b) + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _mean_var(v: list[float]) -> tuple[float, float]:
    n = len(v)
    mean = sum(v) / n
    ss = 0.0
    for x in v:
        ss += (x - mean) * (x - mean)
    return mean, ss / (n - 1)


def ab_test(groups: list[str | None], values: list[float | None], a: str, b: str, *, force_python: bool = False) -> dict[str, Any]:
    """Welch t-test (sample variance, two-sided p via the Student t CDF); None when n < 2 or both variances are 0."""
    if USING_RUST and not force_python:
        return dict(_core.ab_test(groups, values, a, b))
    va = [f for g, v in zip(groups, values, strict=True) if g == a and (f := _fin(v)) is not None]
    vb = [f for g, v in zip(groups, values, strict=True) if g == b and (f := _fin(v)) is not None]
    na, nb = len(va), len(vb)
    out: dict[str, Any] = {"n_a": na, "n_b": nb, "mean_a": None, "mean_b": None, "diff": None, "std_a": None, "std_b": None,
                           "welch_t": None, "df": None, "p_value": None, "cohens_d": None}
    if na < 2 or nb < 2:
        return out
    (ma, vara), (mb, varb) = _mean_var(va), _mean_var(vb)
    if vara == 0.0 and varb == 0.0:
        return out
    fa, fb = float(na), float(nb)
    diff = ma - mb
    sa, sb = vara / fa, varb / fb
    t = diff / math.sqrt(sa + sb)
    df = (sa + sb) * (sa + sb) / (sa * sa / (fa - 1.0) + sb * sb / (fb - 1.0))
    p = _betai(df / 2.0, 0.5, df / (df + t * t))
    pooled = math.sqrt(((fa - 1.0) * vara + (fb - 1.0) * varb) / (fa + fb - 2.0))
    out.update({"mean_a": ma, "mean_b": mb, "diff": diff, "std_a": math.sqrt(vara), "std_b": math.sqrt(varb), "welch_t": t, "df": df,
                "p_value": p, "cohens_d": diff / pooled})
    return out


SEASON_PERIODS = ("month", "weekday", "quarter", "hour")


def _calendar_component(t: int, period: str) -> int:
    import datetime as dt

    if period == "hour":
        return (t % 86_400) // 3600
    d = dt.datetime.fromtimestamp(t, tz=dt.UTC)
    if period == "weekday":
        return d.isoweekday()
    if period == "quarter":
        return (d.month - 1) // 3 + 1
    return d.month


def seasonality(ts: list[int | None], values: list[float | None] | None, period: str, *, force_python: bool = False) -> list[dict[str, Any]]:
    """Per UTC calendar bucket: count, mean and index (bucket mean / overall mean; count share when values is None)."""
    if period not in SEASON_PERIODS:
        raise ValueError(f"unknown period {period!r}")
    if USING_RUST and not force_python:
        return [dict(d) for d in _core.seasonality(ts, values, period)]
    acc: dict[int, list[float]] = {}
    for i, t in enumerate(ts):
        if t is None:
            continue
        if values is None:
            v = 1.0
        else:
            v = _fin(values[i])
            if v is None:
                continue
        e = acc.setdefault(_calendar_component(int(t), period), [0, 0.0])
        e[0] += 1
        e[1] += v
    total_count = sum(e[0] for e in acc.values())
    total_sum = sum(e[1] for e in acc.values())
    n_buckets = float(len(acc))
    out = []
    for p in sorted(acc):
        c, s = acc[p]
        if values is not None:
            overall = total_sum / total_count
            mean, index = s / c, (None if overall == 0.0 else (s / c) / overall)
        else:
            mean, index = None, c / (total_count / n_buckets)
        out.append({"period": p, "count": c, "mean": mean, "index": index})
    return out


def engine() -> str:
    return "rust" if USING_RUST else "python-fallback"


# ---------------------------------------------------------------------------------------------
# Arrow transfer: the caller hands pyarrow arrays (Float64 / Int64 / string) straight to Rust.
# Same kernels, same results as the list functions above — only the hand-off differs.
# ---------------------------------------------------------------------------------------------

def arrow_available() -> bool:
    return USING_RUST and ARROW_SUPPORT


class ArrowKernels:
    """Thin namespace so callers can write `arrow.describe(arr)`; raises if Arrow support is absent."""

    @staticmethod
    def _need():
        if not arrow_available():
            raise RuntimeError("arrow transfer requires the mcpcluster_core wheel with ARROW_SUPPORT")

    @classmethod
    def describe(cls, values) -> dict[str, Any]:
        cls._need()
        return dict(_core.describe_arrow(values))

    @classmethod
    def percentiles(cls, values, ps: list[float]) -> list[float | None]:
        cls._need()
        return list(_core.percentiles_arrow(values, [float(p) for p in ps]))

    @classmethod
    def histogram(cls, values, bins: int) -> dict[str, Any]:
        cls._need()
        return dict(_core.histogram_arrow(values, int(bins)))

    @classmethod
    def group_stats(cls, keys, values) -> list[dict[str, Any]]:
        cls._need()
        return [dict(d) for d in _core.group_stats_arrow(keys, values)]

    @classmethod
    def top_k(cls, keys, k: int) -> list[dict[str, Any]]:
        cls._need()
        return [dict(d) for d in _core.top_k_arrow(keys, int(k))]

    @classmethod
    def distinct_count(cls, keys) -> int:
        cls._need()
        return int(_core.distinct_count_arrow(keys))

    @classmethod
    def correlation(cls, x, y) -> dict[str, Any]:
        cls._need()
        return dict(_core.correlation_arrow(x, y))

    @classmethod
    def outliers(cls, values, method: str, threshold: float) -> dict[str, Any]:
        cls._need()
        return dict(_core.outliers_arrow(values, method, float(threshold)))

    @classmethod
    def time_bucket(cls, ts, values, bucket_secs: int, agg: str) -> list[dict[str, Any]]:
        cls._need()
        return [dict(d) for d in _core.time_bucket_arrow(ts, values, int(bucket_secs), agg)]

    @classmethod
    def moving_average(cls, values, window: int) -> list[float | None]:
        cls._need()
        return list(_core.moving_average_arrow(values, int(window)))

    @classmethod
    def linear_trend(cls, y, x=None, horizon: int = 0) -> dict[str, Any]:
        cls._need()
        r = dict(_core.linear_trend_arrow(y, x, int(horizon)))
        r["forecast"] = [dict(p) for p in r["forecast"]]
        return r

    @classmethod
    def growth(cls, values, lag: int) -> dict[str, Any]:
        cls._need()
        r = dict(_core.growth_arrow(values, int(lag)))
        r["rows"] = [dict(d) for d in r["rows"]]
        return r

    @classmethod
    def pareto(cls, keys, values, threshold: float) -> dict[str, Any]:
        cls._need()
        r = dict(_core.pareto_arrow(keys, values, float(threshold)))
        r["rows"] = [dict(d) for d in r["rows"]]
        return r

    @classmethod
    def ab_test(cls, groups, values, a: str, b: str) -> dict[str, Any]:
        cls._need()
        return dict(_core.ab_test_arrow(groups, values, a, b))

    @classmethod
    def seasonality(cls, ts, values, period: str) -> list[dict[str, Any]]:
        cls._need()
        return [dict(d) for d in _core.seasonality_arrow(ts, values, period)]


arrow = ArrowKernels
