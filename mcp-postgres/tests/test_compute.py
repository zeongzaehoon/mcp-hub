"""Rust compute kernels vs the Python mirror: known values + parity on every function."""

import math
import os

import pytest

from mcpcluster_common import compute as cp

V = [1.0, 2.0, 2.0, 3.0, 4.0, 100.0, None, float("nan")]
K = ["a", "b", "a", None, "a", "b", "c", "c"]
T = [0, 30, 3599, 3600, 7200, None, 7250, 7300]


def approx(a, b, tol=1e-9):
    if a is None or b is None:
        return a is b
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(approx(a[k], b[k], tol) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(approx(x, y, tol) for x, y in zip(a, b, strict=True))
    if isinstance(a, float) or isinstance(b, float):
        return math.isclose(float(a), float(b), rel_tol=tol, abs_tol=tol)
    return a == b


def test_describe_python_semantics():
    d = cp.describe(V, force_python=True)
    assert d["count"] == 6 and d["nulls"] == 2 and d["min"] == 1 and d["max"] == 100 and d["p50"] == 2.5
    assert math.isclose(d["mean"], 112 / 6) and d["std"] is not None
    assert cp.describe([], force_python=True)["mean"] is None and cp.describe([5.0], force_python=True)["std"] is None


def test_percentiles_linear_interpolation():
    assert cp.percentiles([1, 2, 3, 4], [0, 50, 100, 75], force_python=True) == [1.0, 2.5, 4.0, 3.25]


def test_histogram_and_groups_and_topk():
    h = cp.histogram([0, 1, 2, 3, 4, 10], 5, force_python=True)
    assert h["counts"] == [2, 2, 1, 0, 1] and h["edges"][0] == 0 and h["edges"][-1] == 10  # width 2: [0,2) [2,4) [4,6) [6,8) [8,10]
    g = cp.group_stats(K, V, force_python=True)
    assert [r["key"] for r in g] == ["a", "b", "c", "<null>"] and g[0]["count"] == 3 and g[0]["sum"] == 7 and g[2]["count"] == 0 and g[2]["nulls"] == 2
    assert cp.top_k(K, 2, force_python=True) == [{"key": "a", "count": 3}, {"key": "b", "count": 2}]
    assert cp.distinct_count(K, force_python=True) == 4


def test_correlation_and_outliers():
    c = cp.correlation([1, 2, 3, 4, None], [2, 4, 6, 8, 1], force_python=True)
    assert c["n"] == 4 and math.isclose(c["pearson"], 1.0) and math.isclose(c["spearman"], 1.0)
    o = cp.outliers(V, "iqr", 1.5, force_python=True)
    assert o["indices"] == [5] and o["count"] == 1
    z = cp.outliers([1, 1, 1, 1, 50], "zscore", 1.5, force_python=True)
    assert z["indices"] == [4]


def test_time_bucket_and_moving_average():
    tb = cp.time_bucket(T, V, 3600, "sum", force_python=True)
    assert [(r["bucket_epoch"], r["count"], r["value"]) for r in tb] == [(0, 3, 5.0), (3600, 1, 3.0), (7200, 3, 4.0)]  # t=None rows are skipped; NaN counts as null
    assert cp.moving_average([1, 2, 3, None, 5, 6], 2, force_python=True) == [None, 1.5, 2.5, None, None, 5.5]


def test_linear_trend_ols_and_forecast():
    r = cp.linear_trend([1, 3, 5, 7], None, 2, force_python=True)  # y = 2x + 1 on the row index
    assert r["n"] == 4 and r["slope"] == 2.0 and r["intercept"] == 1.0 and r["r2"] == 1.0
    assert r["forecast"] == [{"x": 4.0, "y": 9.0}, {"x": 5.0, "y": 11.0}]
    r = cp.linear_trend([1, 3, None, 5, 9], [0, 1, 2, 2, 4], 1, force_python=True)  # null y dropped pairwise; step = (4-0)/3
    assert r["n"] == 4 and approx(r["slope"], 2.0) and approx(r["forecast"], [{"x": 4 + 4 / 3, "y": 2 * (4 + 4 / 3) + 1}])
    assert cp.linear_trend([1, 2], [5, 5], 3, force_python=True) == {"n": 2, "slope": None, "intercept": None, "r2": None, "forecast": []}
    assert cp.linear_trend([7], None, 3, force_python=True)["slope"] is None
    assert cp.linear_trend([4, 4, 4], None, 0, force_python=True)["r2"] is None  # constant y: slope 0, r2 undefined


def test_growth_lag_and_total():
    g = cp.growth([100, 110, None, 99], 1, force_python=True)
    assert [(r["prev"], r["abs_change"], r["pct_change"]) for r in g["rows"]] == [(None, None, None), (100.0, 10.0, 10.0), (110.0, None, None), (None, None, None)]
    assert (g["first"], g["last"], g["total_pct_change"]) == (100.0, 99.0, -1.0)
    g = cp.growth([0, 5, -10], 1, force_python=True)
    assert g["rows"][1]["abs_change"] == 5.0 and g["rows"][1]["pct_change"] is None  # prev == 0
    assert g["rows"][2]["pct_change"] == -300.0 and g["total_pct_change"] is None  # |prev| in the denominator; first == 0
    assert [r["prev"] for r in cp.growth([1, 2, 3, 4], 2, force_python=True)["rows"]] == [None, None, 1.0, 2.0]


def test_pareto_shares_and_threshold():
    r = cp.pareto(["a", "b", "c", "a", None], [50, 30, 10, 10, None], 0.8, force_python=True)
    assert r["total"] == 100.0 and [(x["key"], x["sum"], x["share"]) for x in r["rows"]] == [("a", 60.0, 0.6), ("b", 30.0, 0.3), ("c", 10.0, 0.1)]
    assert approx([x["cum_share"] for x in r["rows"]], [0.6, 0.9, 1.0]) and r["keys_to_threshold"] == 2
    assert cp.pareto(["a", "b", "c", "a"], [50, 30, 10, 10], 1.0, force_python=True)["keys_to_threshold"] == 3  # 0.6+0.3+0.1 rounds below 1.0
    assert cp.pareto(["a", "b"], [1, 1], 0.5, force_python=True)["rows"][0]["key"] == "a"  # ties: key asc
    assert cp.pareto(["a"], [-1], 0.5, force_python=True) == {"total": -1.0, "rows": [{"key": "a", "sum": -1.0, "share": None, "cum_share": None}], "keys_to_threshold": None}


def test_ab_test_welch_known_values():
    # a = 1..5 (mean 3, var 2.5), b = 2,4,..,10 (mean 6, var 10): se^2 = 0.5 + 2 = 2.5, t = -3/sqrt(2.5),
    # df = 2.5^2 / (0.5^2/4 + 2^2/4) = 6.25/1.0625, pooled sd = sqrt((4*2.5 + 4*10)/8) = 2.5 -> d = -1.2.
    # p = 0.10753119493062724 is scipy.stats.ttest_ind(equal_var=False).pvalue for the same inputs.
    r = cp.ab_test(["a"] * 5 + ["b"] * 5 + ["c", "a"], [1, 2, 3, 4, 5, 2, 4, 6, 8, 10, 99, None], "a", "b", force_python=True)
    assert (r["n_a"], r["n_b"], r["mean_a"], r["mean_b"], r["diff"], r["cohens_d"]) == (5, 5, 3.0, 6.0, -3.0, -1.2)
    assert approx(r["std_a"], math.sqrt(2.5)) and approx(r["std_b"], math.sqrt(10))
    assert approx(r["welch_t"], -3 / math.sqrt(2.5)) and approx(r["df"], 6.25 / 1.0625) and approx(r["p_value"], 0.10753119493062724)
    # unequal sizes, non-integer df: scipy gives t=-2.2282441862546114, df=5.347586766693954, p=0.0728512608536944
    r = cp.ab_test(["a"] * 6 + ["b"] * 4, [12.1, 14.3, 11.8, 13.9, 15.2, 12.7, 13.4, 16.8, 15.1, 17.2], "a", "b", force_python=True)
    assert approx(r["welch_t"], -2.2282441862546114) and approx(r["df"], 5.347586766693954) and approx(r["p_value"], 0.0728512608536944)
    assert cp.ab_test(["a", "a", "b", "b"], [1, 1, 2, 2], "a", "b", force_python=True)["p_value"] is None  # both variances 0
    assert cp.ab_test(["a", "b", "b"], [1, 2, 3], "a", "b", force_python=True)["welch_t"] is None  # n_a < 2
    assert cp.ab_test(["a", "a", "b", "b"], [1, 2, 1.5, 1.5], "a", "b", force_python=True)["p_value"] == 1.0  # t == 0
    assert approx(cp._gammln(0.5), math.lgamma(0.5), 1e-13) and approx(cp._gammln(100.5), math.lgamma(100.5), 1e-13)


def test_seasonality_calendar_buckets():
    import datetime as dt

    def at(*a):
        return int(dt.datetime(*a, tzinfo=dt.UTC).timestamp())

    ts = [at(2026, 1, 5), at(2026, 1, 6), at(2026, 4, 1), at(2026, 7, 31, 23), None, at(2026, 1, 7)]  # Mon, Tue, Wed, Fri, -, Wed
    vals = [10, 20, 30, 40, 50, None]
    assert cp.seasonality(ts, vals, "month", force_python=True) == [{"period": 1, "count": 2, "mean": 15.0, "index": 0.6}, {"period": 4, "count": 1, "mean": 30.0, "index": 1.2},
                                                                     {"period": 7, "count": 1, "mean": 40.0, "index": 1.6}]
    assert [(r["period"], r["mean"]) for r in cp.seasonality(ts, vals, "weekday", force_python=True)] == [(1, 10.0), (2, 20.0), (3, 30.0), (5, 40.0)]
    assert [(r["period"], r["count"]) for r in cp.seasonality(ts, vals, "quarter", force_python=True)] == [(1, 2), (2, 1), (3, 1)]
    assert [(r["period"], r["count"]) for r in cp.seasonality(ts, vals, "hour", force_python=True)] == [(0, 3), (23, 1)]
    counts = cp.seasonality(ts, None, "month", force_python=True)  # 5 timestamped rows over 3 buckets: average 5/3 per bucket
    assert counts == [{"period": 1, "count": 3, "mean": None, "index": 3 / (5 / 3)}, {"period": 4, "count": 1, "mean": None, "index": 1 / (5 / 3)},
                      {"period": 7, "count": 1, "mean": None, "index": 1 / (5 / 3)}]
    assert cp.seasonality([at(1969, 12, 31, 23), at(1969, 12, 31)], [1, 1], "weekday", force_python=True)[0]["period"] == 3  # pre-epoch: Wednesday
    assert cp.seasonality([at(2026, 1, 1)], [0.0], "month", force_python=True)[0]["index"] is None  # overall mean 0
    with pytest.raises(ValueError):
        cp.seasonality([0], [1], "year", force_python=True)


@pytest.mark.skipif(not cp.USING_RUST and not os.environ.get("EXPECT_RUST"), reason="rust wheel not installed")
def test_rust_parity():
    assert cp.USING_RUST, "EXPECT_RUST set but compute kernels not importable"
    big = [float(i % 97) * 1.5 if i % 13 else None for i in range(50_000)]
    keys = [str(i % 11) if i % 7 else None for i in range(50_000)]
    ts = [i * 37 if i % 5 else None for i in range(50_000)]
    wide_ts = [(i - 25_000) * 37_000 if i % 5 else None for i in range(50_000)]  # ~1940..1999, exercises civil_from_days
    checks = [
        (cp.describe(V), cp.describe(V, force_python=True)),
        (cp.describe(big), cp.describe(big, force_python=True)),
        (cp.percentiles(big, [1, 25, 50, 75, 99.9]), cp.percentiles(big, [1, 25, 50, 75, 99.9], force_python=True)),
        (cp.histogram(big, 17), cp.histogram(big, 17, force_python=True)),
        (cp.group_stats(keys, big), cp.group_stats(keys, big, force_python=True)),
        (cp.top_k(keys, 5), cp.top_k(keys, 5, force_python=True)),
        (cp.distinct_count(keys), cp.distinct_count(keys, force_python=True)),
        (cp.correlation(big, big[::-1]), cp.correlation(big, big[::-1], force_python=True)),
        (cp.outliers(big, "iqr", 1.5), cp.outliers(big, "iqr", 1.5, force_python=True)),
        (cp.outliers(big, "zscore", 2.0), cp.outliers(big, "zscore", 2.0, force_python=True)),
        (cp.time_bucket(ts, big, 3600, "mean"), cp.time_bucket(ts, big, 3600, "mean", force_python=True)),
        (cp.moving_average(big[:5000], 7), cp.moving_average(big[:5000], 7, force_python=True)),
        (cp.linear_trend(big, None, 5), cp.linear_trend(big, None, 5, force_python=True)),
        (cp.linear_trend(big, wide_ts, 3), cp.linear_trend(big, wide_ts, 3, force_python=True)),
        (cp.growth(big[:5000], 3), cp.growth(big[:5000], 3, force_python=True)),
        (cp.pareto(keys, big, 0.8), cp.pareto(keys, big, 0.8, force_python=True)),
        (cp.ab_test(keys, big, "1", "2"), cp.ab_test(keys, big, "1", "2", force_python=True)),
        (cp.seasonality(wide_ts, big, "month"), cp.seasonality(wide_ts, big, "month", force_python=True)),
        (cp.seasonality(wide_ts, big, "quarter"), cp.seasonality(wide_ts, big, "quarter", force_python=True)),
        (cp.seasonality(wide_ts, big, "hour"), cp.seasonality(wide_ts, big, "hour", force_python=True)),
        (cp.seasonality(wide_ts, None, "weekday"), cp.seasonality(wide_ts, None, "weekday", force_python=True)),
    ]
    for i, (rust, py) in enumerate(checks):
        assert approx(rust, py), f"check #{i} diverged: {str(rust)[:200]} vs {str(py)[:200]}"
    assert cp.engine() == "rust"


def test_choose_transfer_rules():
    from mcpcluster_common.compute_spec import choose_transfer

    if cp.arrow_available():
        assert choose_transfer("auto", 10, 20_000) == "copy" and choose_transfer("auto", 20_000, 20_000) == "arrow"
        assert choose_transfer("arrow", 1, 20_000) == "arrow" and choose_transfer("copy", 10**9, 20_000) == "copy"
    else:
        assert choose_transfer("arrow", 10**9, 20_000) == "copy"  # no wheel: always copy


@pytest.mark.skipif(not cp.arrow_available() and not os.environ.get("EXPECT_RUST"), reason="arrow support not built")
def test_arrow_transfer_matches_copy_transfer():
    """Same kernels, different hand-off: results must be identical (this is the whole point)."""
    import polars as pl

    from mcpcluster_common import compute_spec as cs

    assert cp.arrow_available(), "EXPECT_RUST set but ARROW_SUPPORT missing"
    n = 60_000
    df = pl.DataFrame({
        "v": [float(i % 97) * 1.5 if i % 13 else None for i in range(n)],
        "k": [str(i % 11) if i % 7 else None for i in range(n)],
        "t": [i * 37 if i % 5 else None for i in range(n)],
        "w": [float((i * 7) % 31) for i in range(n)],
    })
    specs = [
        {"op": "describe", "column": "v"}, {"op": "percentiles", "column": "v", "ps": [5, 50, 95]},
        {"op": "histogram", "column": "v", "bins": 12}, {"op": "group_stats", "key": "k", "value": "v"},
        {"op": "top_k", "column": "k", "k": 4}, {"op": "distinct_count", "column": "k"},
        {"op": "correlation", "x": "v", "y": "w"}, {"op": "outliers", "column": "v", "method": "zscore", "threshold": 1.0},
        {"op": "time_bucket", "time": "t", "value": "v", "bucket": "1h", "agg": "mean"},
        {"op": "moving_average", "column": "v", "window": 9, "limit": 500},
        {"op": "linear_trend", "y": "v", "x": "t", "horizon": 4}, {"op": "linear_trend", "y": "w"},
        {"op": "growth", "column": "v", "order_by": "t", "lag": 2, "limit": 300}, {"op": "pareto", "key": "k", "value": "w"},
        {"op": "ab_test", "group": "k", "value": "v", "a": "1", "b": "2"},
        {"op": "seasonality", "time": "t", "value": "v", "period": "hour"}, {"op": "seasonality", "time": "t", "period": "weekday"},
    ]
    for raw in specs:
        spec = cs.parse_compute(raw)
        copy_res, used_c = cs.run_compute(df, spec, transfer="copy")
        arrow_res, used_a = cs.run_compute(df, spec, transfer="arrow")
        assert (used_c, used_a) == ("copy", "arrow"), raw
        assert approx(copy_res, arrow_res), f"{raw['op']} diverged between transfers"
    # auto: threshold decides
    assert cs.run_compute(df, cs.parse_compute(specs[0]), transfer="auto", arrow_threshold=100_000)[1] == "copy"
    assert cs.run_compute(df, cs.parse_compute(specs[0]), transfer="auto", arrow_threshold=1_000)[1] == "arrow"
    # a datetime column reaches the arrow path as Int64 epoch seconds
    dfd = pl.DataFrame({"at": pl.Series(["2026-09-01T10:00:00", "2026-09-01T10:30:00", None]).str.to_datetime(), "v": [1.0, 2.0, 3.0]})
    res, used = cs.run_compute(dfd, cs.parse_compute({"op": "time_bucket", "time": "at", "value": "v", "bucket": "1h", "agg": "sum"}), transfer="arrow")
    assert used == "arrow" and res == [{"bucket_epoch": 1788256800, "count": 2, "value": 3.0, "bucket": "2026-09-01T10:00:00+00:00"}]
    res, used = cs.run_compute(dfd, cs.parse_compute({"op": "linear_trend", "y": "v", "x": "at", "horizon": 1}), transfer="arrow")
    assert used == "arrow" and res["n"] == 2 and approx(res["slope"], 1 / 1800) and res["forecast"][0]["x"] == 1788256800 + 1800 + 1800
