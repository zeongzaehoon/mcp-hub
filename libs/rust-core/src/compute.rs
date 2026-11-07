//! Numeric / statistical kernels for the `compute` MCP tool. Inputs arrive as Python lists
//! (None = null); everything heavy runs in Rust with rayon. Semantics are mirrored 1:1 by
//! `mcpcluster_common/compute.py` (Python fallback) and checked by parity tests:
//!   * percentiles: linear interpolation between closest ranks (numpy "linear" / R type 7)
//!   * std: sample standard deviation (n - 1); undefined (None) for n < 2
//!   * group / top-k output ordered by count desc, then key asc (deterministic)

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rayon::prelude::*;
use std::collections::HashMap;

fn clean(values: &[Option<f64>]) -> Vec<f64> {
    values.iter().filter_map(|v| v.filter(|x| x.is_finite())).collect()
}

fn sorted(mut v: Vec<f64>) -> Vec<f64> {
    v.par_sort_unstable_by(|a, b| a.partial_cmp(b).unwrap());
    v
}

fn percentile_sorted(s: &[f64], p: f64) -> Option<f64> {
    if s.is_empty() {
        return None;
    }
    let p = p.clamp(0.0, 100.0);
    let rank = (p / 100.0) * (s.len() as f64 - 1.0);
    let lo = rank.floor() as usize;
    let hi = rank.ceil() as usize;
    let frac = rank - lo as f64;
    Some(s[lo] + (s[hi] - s[lo]) * frac)
}

fn mean_std(v: &[f64]) -> (Option<f64>, Option<f64>) {
    let n = v.len();
    if n == 0 {
        return (None, None);
    }
    let sum: f64 = v.par_iter().sum();
    let mean = sum / n as f64;
    if n < 2 {
        return (Some(mean), None);
    }
    let ss: f64 = v.par_iter().map(|x| (x - mean) * (x - mean)).sum();
    (Some(mean), Some((ss / (n as f64 - 1.0)).sqrt()))
}

#[pyfunction]
pub fn describe<'py>(py: Python<'py>, values: Vec<Option<f64>>) -> PyResult<Bound<'py, PyDict>> {
    let nulls = values.iter().filter(|v| v.is_none() || !v.unwrap().is_finite()).count();
    let s = py.allow_threads(|| sorted(clean(&values)));
    let (mean, std) = py.allow_threads(|| mean_std(&s));
    let d = PyDict::new_bound(py);
    d.set_item("count", s.len())?;
    d.set_item("nulls", nulls)?;
    d.set_item("mean", mean)?;
    d.set_item("std", std)?;
    d.set_item("min", s.first().copied())?;
    d.set_item("max", s.last().copied())?;
    d.set_item("sum", if s.is_empty() { None } else { Some(s.iter().sum::<f64>()) })?;
    for (name, p) in [("p25", 25.0), ("p50", 50.0), ("p75", 75.0), ("p95", 95.0), ("p99", 99.0)] {
        d.set_item(name, percentile_sorted(&s, p))?;
    }
    Ok(d)
}

#[pyfunction]
pub fn percentiles(py: Python<'_>, values: Vec<Option<f64>>, ps: Vec<f64>) -> Vec<Option<f64>> {
    py.allow_threads(|| {
        let s = sorted(clean(&values));
        ps.iter().map(|p| percentile_sorted(&s, *p)).collect()
    })
}

/// Equal-width histogram over [min, max]; the last bin is closed on the right.
#[pyfunction]
pub fn histogram<'py>(py: Python<'py>, values: Vec<Option<f64>>, bins: usize) -> PyResult<Bound<'py, PyDict>> {
    let bins = bins.clamp(1, 10_000);
    let (edges, counts) = py.allow_threads(|| {
        let v = clean(&values);
        if v.is_empty() {
            return (vec![], vec![]);
        }
        let min = v.iter().cloned().fold(f64::INFINITY, f64::min);
        let max = v.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let width = if max > min { (max - min) / bins as f64 } else { 1.0 };
        let edges: Vec<f64> = (0..=bins).map(|i| min + width * i as f64).collect();
        let counts = v
            .par_iter()
            .fold(
                || vec![0u64; bins],
                |mut acc, x| {
                    let mut i = ((x - min) / width).floor() as usize;
                    if i >= bins {
                        i = bins - 1;
                    }
                    acc[i] += 1;
                    acc
                },
            )
            .reduce(|| vec![0u64; bins], |a, b| a.iter().zip(b.iter()).map(|(x, y)| x + y).collect());
        (edges, counts)
    });
    let d = PyDict::new_bound(py);
    d.set_item("edges", edges)?;
    d.set_item("counts", counts)?;
    Ok(d)
}

#[derive(Default, Clone)]
struct Acc {
    count: u64,
    nulls: u64,
    sum: f64,
    min: f64,
    max: f64,
}

fn merge(mut a: HashMap<String, Acc>, b: HashMap<String, Acc>) -> HashMap<String, Acc> {
    for (k, v) in b {
        let e = a.entry(k).or_insert_with(|| Acc { min: f64::INFINITY, max: f64::NEG_INFINITY, ..Default::default() });
        e.count += v.count;
        e.nulls += v.nulls;
        e.sum += v.sum;
        e.min = e.min.min(v.min);
        e.max = e.max.max(v.max);
    }
    a
}

/// Per-group count / sum / mean / min / max of `values` keyed by `keys` (None key => "<null>").
#[pyfunction]
pub fn group_stats<'py>(py: Python<'py>, keys: Vec<Option<String>>, values: Vec<Option<f64>>) -> PyResult<Bound<'py, PyList>> {
    let groups = py.allow_threads(|| {
        keys.par_iter()
            .zip(values.par_iter())
            .fold(HashMap::<String, Acc>::new, |mut m, (k, v)| {
                let key = k.clone().unwrap_or_else(|| "<null>".to_string());
                let e = m.entry(key).or_insert_with(|| Acc { min: f64::INFINITY, max: f64::NEG_INFINITY, ..Default::default() });
                match v {
                    Some(x) if x.is_finite() => {
                        e.count += 1;
                        e.sum += x;
                        e.min = e.min.min(*x);
                        e.max = e.max.max(*x);
                    }
                    _ => e.nulls += 1,
                }
                m
            })
            .reduce(HashMap::new, merge)
    });
    let mut rows: Vec<(String, Acc)> = groups.into_iter().collect();
    rows.sort_by(|a, b| (b.1.count + b.1.nulls).cmp(&(a.1.count + a.1.nulls)).then_with(|| a.0.cmp(&b.0)));
    let out = PyList::empty_bound(py);
    for (k, a) in rows {
        let d = PyDict::new_bound(py);
        d.set_item("key", k)?;
        d.set_item("count", a.count)?;
        d.set_item("nulls", a.nulls)?;
        d.set_item("sum", if a.count > 0 { Some(a.sum) } else { None })?;
        d.set_item("mean", if a.count > 0 { Some(a.sum / a.count as f64) } else { None })?;
        d.set_item("min", if a.count > 0 { Some(a.min) } else { None })?;
        d.set_item("max", if a.count > 0 { Some(a.max) } else { None })?;
        out.append(d)?;
    }
    Ok(out)
}

/// Most frequent values (None => "<null>"), count desc then key asc.
#[pyfunction]
pub fn top_k<'py>(py: Python<'py>, keys: Vec<Option<String>>, k: usize) -> PyResult<Bound<'py, PyList>> {
    let counts = py.allow_threads(|| {
        keys.par_iter()
            .fold(HashMap::<String, u64>::new, |mut m, key| {
                *m.entry(key.clone().unwrap_or_else(|| "<null>".to_string())).or_insert(0) += 1;
                m
            })
            .reduce(HashMap::new, |mut a, b| {
                for (key, c) in b {
                    *a.entry(key).or_insert(0) += c;
                }
                a
            })
    });
    let mut rows: Vec<(String, u64)> = counts.into_iter().collect();
    rows.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    let out = PyList::empty_bound(py);
    for (key, c) in rows.into_iter().take(k.max(1)) {
        let d = PyDict::new_bound(py);
        d.set_item("key", key)?;
        d.set_item("count", c)?;
        out.append(d)?;
    }
    Ok(out)
}

#[pyfunction]
pub fn distinct_count(py: Python<'_>, keys: Vec<Option<String>>) -> usize {
    py.allow_threads(|| {
        let set: std::collections::HashSet<&Option<String>> = keys.iter().collect();
        set.len()
    })
}

fn ranks(v: &[f64]) -> Vec<f64> {
    // average ranks for ties (Spearman)
    let mut idx: Vec<usize> = (0..v.len()).collect();
    idx.sort_by(|a, b| v[*a].partial_cmp(&v[*b]).unwrap());
    let mut r = vec![0.0; v.len()];
    let mut i = 0;
    while i < idx.len() {
        let mut j = i;
        while j + 1 < idx.len() && v[idx[j + 1]] == v[idx[i]] {
            j += 1;
        }
        let avg = (i + j) as f64 / 2.0 + 1.0;
        for k in i..=j {
            r[idx[k]] = avg;
        }
        i = j + 1;
    }
    r
}

fn pearson(x: &[f64], y: &[f64]) -> Option<f64> {
    let n = x.len();
    if n < 2 {
        return None;
    }
    let mx = x.iter().sum::<f64>() / n as f64;
    let my = y.iter().sum::<f64>() / n as f64;
    let (mut sxy, mut sxx, mut syy) = (0.0, 0.0, 0.0);
    for i in 0..n {
        let dx = x[i] - mx;
        let dy = y[i] - my;
        sxy += dx * dy;
        sxx += dx * dx;
        syy += dy * dy;
    }
    if sxx == 0.0 || syy == 0.0 {
        return None;
    }
    Some(sxy / (sxx * syy).sqrt())
}

/// Pearson and Spearman correlation over rows where both values are present.
#[pyfunction]
pub fn correlation<'py>(py: Python<'py>, x: Vec<Option<f64>>, y: Vec<Option<f64>>) -> PyResult<Bound<'py, PyDict>> {
    let (n, p, s) = py.allow_threads(|| {
        let pairs: Vec<(f64, f64)> = x
            .iter()
            .zip(y.iter())
            .filter_map(|(a, b)| match (a, b) {
                (Some(a), Some(b)) if a.is_finite() && b.is_finite() => Some((*a, *b)),
                _ => None,
            })
            .collect();
        let xs: Vec<f64> = pairs.iter().map(|p| p.0).collect();
        let ys: Vec<f64> = pairs.iter().map(|p| p.1).collect();
        let p = pearson(&xs, &ys);
        let s = pearson(&ranks(&xs), &ranks(&ys));
        (pairs.len(), p, s)
    });
    let d = PyDict::new_bound(py);
    d.set_item("n", n)?;
    d.set_item("pearson", p)?;
    d.set_item("spearman", s)?;
    Ok(d)
}

/// Outlier row positions. method "zscore": |x-mean|/std > threshold; "iqr": outside [q1-t*iqr, q3+t*iqr].
#[pyfunction]
pub fn outliers<'py>(py: Python<'py>, values: Vec<Option<f64>>, method: &str, threshold: f64) -> PyResult<Bound<'py, PyDict>> {
    let (lo, hi, idx) = py.allow_threads(|| {
        let v = clean(&values);
        let (lo, hi) = if method == "iqr" {
            let s = sorted(v.clone());
            match (percentile_sorted(&s, 25.0), percentile_sorted(&s, 75.0)) {
                (Some(q1), Some(q3)) => (q1 - threshold * (q3 - q1), q3 + threshold * (q3 - q1)),
                _ => (f64::NEG_INFINITY, f64::INFINITY),
            }
        } else {
            match mean_std(&v) {
                (Some(m), Some(sd)) => (m - threshold * sd, m + threshold * sd),
                _ => (f64::NEG_INFINITY, f64::INFINITY),
            }
        };
        let idx: Vec<usize> = values
            .par_iter()
            .enumerate()
            .filter_map(|(i, x)| match x {
                Some(x) if x.is_finite() && (*x < lo || *x > hi) => Some(i),
                _ => None,
            })
            .collect();
        (lo, hi, idx)
    });
    let d = PyDict::new_bound(py);
    d.set_item("method", method)?;
    d.set_item("lower", if lo.is_finite() { Some(lo) } else { None })?;
    d.set_item("upper", if hi.is_finite() { Some(hi) } else { None })?;
    d.set_item("count", idx.len())?;
    d.set_item("indices", idx)?;
    Ok(d)
}

/// Time-bucketed aggregation: epoch seconds floored to `bucket_secs`; agg in count|sum|mean|min|max.
#[pyfunction]
pub fn time_bucket<'py>(py: Python<'py>, ts: Vec<Option<i64>>, values: Vec<Option<f64>>, bucket_secs: i64, agg: &str) -> PyResult<Bound<'py, PyList>> {
    let bucket_secs = bucket_secs.max(1);
    let groups = py.allow_threads(|| {
        ts.par_iter()
            .zip(values.par_iter())
            .fold(HashMap::<i64, Acc>::new, |mut m, (t, v)| {
                if let Some(t) = t {
                    let b = t.div_euclid(bucket_secs) * bucket_secs;
                    let e = m.entry(b).or_insert_with(|| Acc { min: f64::INFINITY, max: f64::NEG_INFINITY, ..Default::default() });
                    match v {
                        Some(x) if x.is_finite() => {
                            e.count += 1;
                            e.sum += x;
                            e.min = e.min.min(*x);
                            e.max = e.max.max(*x);
                        }
                        _ => e.nulls += 1,
                    }
                }
                m
            })
            .reduce(HashMap::new, |mut a, b| {
                for (k, v) in b {
                    let e = a.entry(k).or_insert_with(|| Acc { min: f64::INFINITY, max: f64::NEG_INFINITY, ..Default::default() });
                    e.count += v.count;
                    e.nulls += v.nulls;
                    e.sum += v.sum;
                    e.min = e.min.min(v.min);
                    e.max = e.max.max(v.max);
                }
                a
            })
    });
    let mut rows: Vec<(i64, Acc)> = groups.into_iter().collect();
    rows.sort_by_key(|r| r.0);
    let out = PyList::empty_bound(py);
    for (b, a) in rows {
        let value: Option<f64> = match agg {
            "count" => Some((a.count + a.nulls) as f64),
            "sum" => if a.count > 0 { Some(a.sum) } else { None },
            "mean" => if a.count > 0 { Some(a.sum / a.count as f64) } else { None },
            "min" => if a.count > 0 { Some(a.min) } else { None },
            "max" => if a.count > 0 { Some(a.max) } else { None },
            _ => None,
        };
        let d = PyDict::new_bound(py);
        d.set_item("bucket_epoch", b)?;
        d.set_item("count", a.count + a.nulls)?;
        d.set_item("value", value)?;
        out.append(d)?;
    }
    Ok(out)
}

/// Simple moving average; None until the window is full or when any value in the window is null.
#[pyfunction]
pub fn moving_average(py: Python<'_>, values: Vec<Option<f64>>, window: usize) -> Vec<Option<f64>> {
    let w = window.max(1);
    py.allow_threads(|| {
        (0..values.len())
            .into_par_iter()
            .map(|i| {
                if i + 1 < w {
                    return None;
                }
                let slice = &values[i + 1 - w..=i];
                if slice.iter().any(|v| v.is_none() || !v.unwrap().is_finite()) {
                    return None;
                }
                Some(slice.iter().map(|v| v.unwrap()).sum::<f64>() / w as f64)
            })
            .collect()
    })
}

// ---- business-analysis kernels ---------------------------------------------------------------
// Sums below are sequential on purpose: the Python mirror folds left-to-right and the parity
// tolerance (1e-9) must hold through derived quantities such as t statistics and p-values.

fn finite(v: &Option<f64>) -> Option<f64> {
    v.filter(|x| x.is_finite())
}

/// Ordinary least squares y = slope*x + intercept over rows where both are finite. `x` None means the
/// row index 0..n-1 (forecast step 1); otherwise the step is the mean consecutive difference of the kept x.
#[pyfunction]
#[pyo3(signature = (y, x=None, horizon=0))]
pub fn linear_trend<'py>(py: Python<'py>, y: Vec<Option<f64>>, x: Option<Vec<Option<f64>>>, horizon: usize) -> PyResult<Bound<'py, PyDict>> {
    let horizon = horizon.min(1000);
    let index_x = x.is_none();
    let (n, fit, forecast) = py.allow_threads(|| {
        let xs_src: Vec<Option<f64>> = match x {
            Some(v) => v,
            None => (0..y.len()).map(|i| Some(i as f64)).collect(),
        };
        let (mut xs, mut ys) = (Vec::new(), Vec::new());
        for (a, b) in xs_src.iter().zip(y.iter()) {
            if let (Some(a), Some(b)) = (finite(a), finite(b)) {
                xs.push(a);
                ys.push(b);
            }
        }
        let n = xs.len();
        if n < 2 {
            return (n, None, vec![]);
        }
        let mx = xs.iter().sum::<f64>() / n as f64;
        let my = ys.iter().sum::<f64>() / n as f64;
        let (mut sxx, mut sxy, mut syy) = (0.0, 0.0, 0.0);
        for i in 0..n {
            let dx = xs[i] - mx;
            let dy = ys[i] - my;
            sxx += dx * dx;
            sxy += dx * dy;
            syy += dy * dy;
        }
        if sxx == 0.0 {
            return (n, None, vec![]);
        }
        let slope = sxy / sxx;
        let intercept = my - slope * mx;
        let r2 = if syy == 0.0 { None } else { Some(sxy * sxy / (sxx * syy)) };
        let step = if index_x { 1.0 } else { (xs[n - 1] - xs[0]) / (n as f64 - 1.0) };
        let forecast: Vec<(f64, f64)> = (1..=horizon)
            .map(|i| {
                let fx = xs[n - 1] + step * i as f64;
                (fx, slope * fx + intercept)
            })
            .collect();
        (n, Some((slope, intercept, r2)), forecast)
    });
    let d = PyDict::new_bound(py);
    d.set_item("n", n)?;
    d.set_item("slope", fit.map(|f| f.0))?;
    d.set_item("intercept", fit.map(|f| f.1))?;
    d.set_item("r2", fit.and_then(|f| f.2))?;
    let fc = PyList::empty_bound(py);
    for (fx, fy) in forecast {
        let p = PyDict::new_bound(py);
        p.set_item("x", fx)?;
        p.set_item("y", fy)?;
        fc.append(p)?;
    }
    d.set_item("forecast", fc)?;
    Ok(d)
}

/// Period-over-period change with `lag`. pct_change = (value - prev) / |prev| * 100; None when either side is
/// null/non-finite or prev == 0. first/last/total_pct_change use the first and last finite values.
#[pyfunction]
pub fn growth<'py>(py: Python<'py>, values: Vec<Option<f64>>, lag: usize) -> PyResult<Bound<'py, PyDict>> {
    let lag = lag.max(1);
    let rows = PyList::empty_bound(py);
    for i in 0..values.len() {
        let value = finite(&values[i]);
        let prev = if i >= lag { finite(&values[i - lag]) } else { None };
        let (abs_change, pct_change) = match (value, prev) {
            (Some(v), Some(p)) => (Some(v - p), if p == 0.0 { None } else { Some((v - p) / p.abs() * 100.0) }),
            _ => (None, None),
        };
        let d = PyDict::new_bound(py);
        d.set_item("index", i)?;
        d.set_item("value", value)?;
        d.set_item("prev", prev)?;
        d.set_item("abs_change", abs_change)?;
        d.set_item("pct_change", pct_change)?;
        rows.append(d)?;
    }
    let first = values.iter().find_map(finite);
    let last = values.iter().rev().find_map(finite);
    let total = match (first, last) {
        (Some(f), Some(l)) if f != 0.0 => Some((l - f) / f.abs() * 100.0),
        _ => None,
    };
    let d = PyDict::new_bound(py);
    d.set_item("rows", rows)?;
    d.set_item("first", first)?;
    d.set_item("last", last)?;
    d.set_item("total_pct_change", total)?;
    Ok(d)
}

/// Sum of `values` per key (None key => "<null>", null values ignored), sorted by sum desc then key asc, with
/// share and cumulative share of the total. keys_to_threshold = smallest k whose cum_share >= threshold.
#[pyfunction]
pub fn pareto<'py>(py: Python<'py>, keys: Vec<Option<String>>, values: Vec<Option<f64>>, threshold: f64) -> PyResult<Bound<'py, PyDict>> {
    let (total, rows, k_to) = py.allow_threads(|| {
        let mut sums: HashMap<String, f64> = HashMap::new();
        for (k, v) in keys.iter().zip(values.iter()) {
            if let Some(v) = finite(v) {
                *sums.entry(k.clone().unwrap_or_else(|| "<null>".to_string())).or_insert(0.0) += v;
            }
        }
        let mut rows: Vec<(String, f64)> = sums.into_iter().collect();
        rows.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap().then_with(|| a.0.cmp(&b.0)));
        let total: f64 = rows.iter().map(|r| r.1).sum();
        let mut out = Vec::with_capacity(rows.len());
        let mut cum = 0.0;
        let mut k_to: Option<usize> = None;
        for (i, (k, s)) in rows.into_iter().enumerate() {
            let (share, cum_share) = if total > 0.0 {
                cum += s / total;
                (Some(s / total), Some(cum))
            } else {
                (None, None)
            };
            if k_to.is_none() && total > 0.0 && cum >= threshold {
                k_to = Some(i + 1);
            }
            out.push((k, s, share, cum_share));
        }
        if k_to.is_none() && total > 0.0 {
            k_to = Some(out.len()); // rounding kept the last cum_share a hair below 1.0
        }
        (total, out, k_to)
    });
    let list = PyList::empty_bound(py);
    for (k, s, share, cum_share) in rows {
        let d = PyDict::new_bound(py);
        d.set_item("key", k)?;
        d.set_item("sum", s)?;
        d.set_item("share", share)?;
        d.set_item("cum_share", cum_share)?;
        list.append(d)?;
    }
    let d = PyDict::new_bound(py);
    d.set_item("total", total)?;
    d.set_item("rows", list)?;
    d.set_item("keys_to_threshold", k_to)?;
    Ok(d)
}

// Lanczos ln(Gamma) (Numerical Recipes 3rd ed. gammln, |err| ~ 1e-15) and the regularized incomplete beta
// via the modified Lentz continued fraction (betacf). Mirrored verbatim in compute.py so p-values agree.
const GAMMLN_COF: [f64; 14] = [
    57.1562356658629235, -59.5979603554754912, 14.1360979747417471, -0.491913816097620199,
    0.339946499848118887e-4, 0.465236289270485756e-4, -0.983744753048795646e-4, 0.158088703224912494e-3,
    -0.210264441724104883e-3, 0.217439618115212643e-3, -0.164318106536763890e-3, 0.844182239838527433e-4,
    -0.261908384015814087e-4, 0.368991826595316234e-5,
];

fn gammln(xx: f64) -> f64 {
    let x = xx;
    let mut y = xx;
    let mut tmp = x + 5.24218750000000000;
    tmp = (x + 0.5) * tmp.ln() - tmp;
    let mut ser = 0.999999999999997092;
    for c in GAMMLN_COF.iter() {
        y += 1.0;
        ser += c / y;
    }
    tmp + (2.5066282746310005 * ser / x).ln()
}

fn betacf(a: f64, b: f64, x: f64) -> f64 {
    const MAXIT: usize = 300;
    const EPS: f64 = 1e-14;
    const FPMIN: f64 = 1e-300;
    let qab = a + b;
    let qap = a + 1.0;
    let qam = a - 1.0;
    let mut c = 1.0;
    let mut d = 1.0 - qab * x / qap;
    if d.abs() < FPMIN {
        d = FPMIN;
    }
    d = 1.0 / d;
    let mut h = d;
    for m in 1..=MAXIT {
        let m = m as f64;
        let m2 = 2.0 * m;
        let mut aa = m * (b - m) * x / ((qam + m2) * (a + m2));
        d = 1.0 + aa * d;
        if d.abs() < FPMIN {
            d = FPMIN;
        }
        c = 1.0 + aa / c;
        if c.abs() < FPMIN {
            c = FPMIN;
        }
        d = 1.0 / d;
        h *= d * c;
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2));
        d = 1.0 + aa * d;
        if d.abs() < FPMIN {
            d = FPMIN;
        }
        c = 1.0 + aa / c;
        if c.abs() < FPMIN {
            c = FPMIN;
        }
        d = 1.0 / d;
        let del = d * c;
        h *= del;
        if (del - 1.0).abs() < EPS {
            break;
        }
    }
    h
}

/// Regularized incomplete beta I_x(a, b).
fn betai(a: f64, b: f64, x: f64) -> f64 {
    if x <= 0.0 {
        return 0.0;
    }
    if x >= 1.0 {
        return 1.0;
    }
    let bt = (gammln(a + b) - gammln(a) - gammln(b) + a * x.ln() + b * (1.0 - x).ln()).exp();
    if x < (a + 1.0) / (a + b + 2.0) {
        bt * betacf(a, b, x) / a
    } else {
        1.0 - bt * betacf(b, a, 1.0 - x) / b
    }
}

fn mean_var(v: &[f64]) -> (f64, f64) {
    let n = v.len() as f64;
    let mean = v.iter().sum::<f64>() / n;
    let ss: f64 = v.iter().map(|x| (x - mean) * (x - mean)).sum();
    (mean, ss / (n - 1.0))
}

/// Welch two-sample t-test of `values` in group `a` vs group `b` (sample variance, two-sided p from the
/// Student t CDF via betai). Statistics are None when either group has n < 2 or both variances are 0.
#[pyfunction]
pub fn ab_test<'py>(py: Python<'py>, groups: Vec<Option<String>>, values: Vec<Option<f64>>, a: &str, b: &str) -> PyResult<Bound<'py, PyDict>> {
    let pick = |name: &str| -> Vec<f64> {
        groups
            .iter()
            .zip(values.iter())
            .filter_map(|(g, v)| match (g, finite(v)) {
                (Some(g), Some(v)) if g == name => Some(v),
                _ => None,
            })
            .collect()
    };
    let (va, vb) = (pick(a), pick(b));
    let (na, nb) = (va.len(), vb.len());
    let d = PyDict::new_bound(py);
    d.set_item("n_a", na)?;
    d.set_item("n_b", nb)?;
    let stats = if na >= 2 && nb >= 2 {
        let (ma, vara) = mean_var(&va);
        let (mb, varb) = mean_var(&vb);
        if vara == 0.0 && varb == 0.0 {
            None
        } else {
            let (fa, fb) = (na as f64, nb as f64);
            let diff = ma - mb;
            let (sa, sb) = (vara / fa, varb / fb);
            let t = diff / (sa + sb).sqrt();
            let df = (sa + sb) * (sa + sb) / (sa * sa / (fa - 1.0) + sb * sb / (fb - 1.0));
            let p = betai(df / 2.0, 0.5, df / (df + t * t));
            let pooled = (((fa - 1.0) * vara + (fb - 1.0) * varb) / (fa + fb - 2.0)).sqrt();
            Some((ma, mb, diff, vara.sqrt(), varb.sqrt(), t, df, p, diff / pooled))
        }
    } else {
        None
    };
    d.set_item("mean_a", stats.map(|s| s.0))?;
    d.set_item("mean_b", stats.map(|s| s.1))?;
    d.set_item("diff", stats.map(|s| s.2))?;
    d.set_item("std_a", stats.map(|s| s.3))?;
    d.set_item("std_b", stats.map(|s| s.4))?;
    d.set_item("welch_t", stats.map(|s| s.5))?;
    d.set_item("df", stats.map(|s| s.6))?;
    d.set_item("p_value", stats.map(|s| s.7))?;
    d.set_item("cohens_d", stats.map(|s| s.8))?;
    Ok(d)
}

/// Howard Hinnant's civil_from_days: days since 1970-01-01 -> (year, month, day), proleptic Gregorian.
fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn calendar_component(t: i64, period: &str) -> i64 {
    let days = t.div_euclid(86_400);
    match period {
        "hour" => t.rem_euclid(86_400) / 3600,
        "weekday" => (days + 3).rem_euclid(7) + 1, // 1970-01-01 was a Thursday; ISO 1=Mon..7=Sun
        "quarter" => (civil_from_days(days).1 - 1) / 3 + 1,
        _ => civil_from_days(days).1, // month
    }
}

/// Seasonal profile by UTC calendar component of epoch-second `ts`. With `values`: per-bucket count/mean of
/// finite values and index = bucket mean / overall mean (None when the overall mean is 0). Without values:
/// count of rows and index = count / (total count / number of buckets present); mean is None.
#[pyfunction]
#[pyo3(signature = (ts, values, period))]
pub fn seasonality<'py>(py: Python<'py>, ts: Vec<Option<i64>>, values: Option<Vec<Option<f64>>>, period: &str) -> PyResult<Bound<'py, PyList>> {
    if !["month", "weekday", "quarter", "hour"].contains(&period) {
        return Err(pyo3::exceptions::PyValueError::new_err(format!("unknown period {period:?}")));
    }
    let has_values = values.is_some();
    let rows = py.allow_threads(|| {
        let mut acc: std::collections::BTreeMap<i64, (u64, f64)> = std::collections::BTreeMap::new();
        for (i, t) in ts.iter().enumerate() {
            let Some(t) = t else { continue };
            let v = match &values {
                Some(vs) => match finite(&vs[i]) {
                    Some(x) => x,
                    None => continue,
                },
                None => 1.0,
            };
            let e = acc.entry(calendar_component(*t, period)).or_insert((0, 0.0));
            e.0 += 1;
            e.1 += v;
        }
        let total_count: u64 = acc.values().map(|e| e.0).sum();
        let total_sum: f64 = acc.values().map(|e| e.1).sum();
        let n_buckets = acc.len() as f64;
        acc.into_iter()
            .map(|(p, (c, s))| {
                let (mean, index) = if has_values {
                    let overall = total_sum / total_count as f64;
                    (Some(s / c as f64), if overall == 0.0 { None } else { Some((s / c as f64) / overall) })
                } else {
                    (None, Some(c as f64 / (total_count as f64 / n_buckets)))
                };
                (p, c, mean, index)
            })
            .collect::<Vec<_>>()
    });
    let out = PyList::empty_bound(py);
    for (p, c, mean, index) in rows {
        let d = PyDict::new_bound(py);
        d.set_item("period", p)?;
        d.set_item("count", c)?;
        d.set_item("mean", mean)?;
        d.set_item("index", index)?;
        out.append(d)?;
    }
    Ok(out)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(describe, m)?)?;
    m.add_function(wrap_pyfunction!(percentiles, m)?)?;
    m.add_function(wrap_pyfunction!(histogram, m)?)?;
    m.add_function(wrap_pyfunction!(group_stats, m)?)?;
    m.add_function(wrap_pyfunction!(top_k, m)?)?;
    m.add_function(wrap_pyfunction!(distinct_count, m)?)?;
    m.add_function(wrap_pyfunction!(correlation, m)?)?;
    m.add_function(wrap_pyfunction!(outliers, m)?)?;
    m.add_function(wrap_pyfunction!(time_bucket, m)?)?;
    m.add_function(wrap_pyfunction!(moving_average, m)?)?;
    m.add_function(wrap_pyfunction!(linear_trend, m)?)?;
    m.add_function(wrap_pyfunction!(growth, m)?)?;
    m.add_function(wrap_pyfunction!(pareto, m)?)?;
    m.add_function(wrap_pyfunction!(ab_test, m)?)?;
    m.add_function(wrap_pyfunction!(seasonality, m)?)?;
    Ok(())
}
