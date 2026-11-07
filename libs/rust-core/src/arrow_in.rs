//! Arrow C Data Interface entry points. A pyarrow (or polars-exported) array arrives as PyCapsules via
//! `__arrow_c_array__`; we read the buffers directly instead of converting a Python list element by
//! element. The kernels themselves are shared with the list-based functions in `compute.rs`.

use arrow_array::cast::AsArray;
use arrow_array::types::{Float64Type, Int64Type};
use arrow_array::Array;
use arrow_schema::DataType;
use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use pyo3_arrow::PyArray;

use crate::compute;

fn floats(arr: &PyArray) -> PyResult<Vec<Option<f64>>> {
    let a = arr.array();
    match a.data_type() {
        DataType::Float64 => Ok(a.as_primitive::<Float64Type>().iter().collect()),
        DataType::Int64 => Ok(a.as_primitive::<Int64Type>().iter().map(|v| v.map(|x| x as f64)).collect()),
        other => Err(PyTypeError::new_err(format!("expected Float64 or Int64 arrow array, got {other}"))),
    }
}

fn ints(arr: &PyArray) -> PyResult<Vec<Option<i64>>> {
    let a = arr.array();
    match a.data_type() {
        DataType::Int64 => Ok(a.as_primitive::<Int64Type>().iter().collect()),
        DataType::Float64 => Ok(a.as_primitive::<Float64Type>().iter().map(|v| v.map(|x| x as i64)).collect()),
        other => Err(PyTypeError::new_err(format!("expected Int64 arrow array, got {other}"))),
    }
}

fn strings(arr: &PyArray) -> PyResult<Vec<Option<String>>> {
    let a = arr.array();
    match a.data_type() {
        DataType::Utf8 => Ok(a.as_string::<i32>().iter().map(|v| v.map(str::to_owned)).collect()),
        DataType::LargeUtf8 => Ok(a.as_string::<i64>().iter().map(|v| v.map(str::to_owned)).collect()),
        DataType::Utf8View => Ok(a.as_string_view().iter().map(|v| v.map(str::to_owned)).collect()),
        other => Err(PyTypeError::new_err(format!("expected a string arrow array, got {other}"))),
    }
}

#[pyfunction]
fn describe_arrow<'py>(py: Python<'py>, values: PyArray) -> PyResult<Bound<'py, PyDict>> {
    compute::describe(py, floats(&values)?)
}

#[pyfunction]
fn percentiles_arrow(py: Python<'_>, values: PyArray, ps: Vec<f64>) -> PyResult<Vec<Option<f64>>> {
    Ok(compute::percentiles(py, floats(&values)?, ps))
}

#[pyfunction]
fn histogram_arrow<'py>(py: Python<'py>, values: PyArray, bins: usize) -> PyResult<Bound<'py, PyDict>> {
    compute::histogram(py, floats(&values)?, bins)
}

#[pyfunction]
fn group_stats_arrow<'py>(py: Python<'py>, keys: PyArray, values: PyArray) -> PyResult<Bound<'py, PyList>> {
    compute::group_stats(py, strings(&keys)?, floats(&values)?)
}

#[pyfunction]
fn top_k_arrow<'py>(py: Python<'py>, keys: PyArray, k: usize) -> PyResult<Bound<'py, PyList>> {
    compute::top_k(py, strings(&keys)?, k)
}

#[pyfunction]
fn distinct_count_arrow(py: Python<'_>, keys: PyArray) -> PyResult<usize> {
    Ok(compute::distinct_count(py, strings(&keys)?))
}

#[pyfunction]
fn correlation_arrow<'py>(py: Python<'py>, x: PyArray, y: PyArray) -> PyResult<Bound<'py, PyDict>> {
    compute::correlation(py, floats(&x)?, floats(&y)?)
}

#[pyfunction]
fn outliers_arrow<'py>(py: Python<'py>, values: PyArray, method: &str, threshold: f64) -> PyResult<Bound<'py, PyDict>> {
    compute::outliers(py, floats(&values)?, method, threshold)
}

#[pyfunction]
fn time_bucket_arrow<'py>(py: Python<'py>, ts: PyArray, values: PyArray, bucket_secs: i64, agg: &str) -> PyResult<Bound<'py, PyList>> {
    compute::time_bucket(py, ints(&ts)?, floats(&values)?, bucket_secs, agg)
}

#[pyfunction]
fn moving_average_arrow(py: Python<'_>, values: PyArray, window: usize) -> PyResult<Vec<Option<f64>>> {
    Ok(compute::moving_average(py, floats(&values)?, window))
}

#[pyfunction]
#[pyo3(signature = (y, x=None, horizon=0))]
fn linear_trend_arrow<'py>(py: Python<'py>, y: PyArray, x: Option<PyArray>, horizon: usize) -> PyResult<Bound<'py, PyDict>> {
    let x = match x {
        Some(a) => Some(floats(&a)?),
        None => None,
    };
    compute::linear_trend(py, floats(&y)?, x, horizon)
}

#[pyfunction]
fn growth_arrow<'py>(py: Python<'py>, values: PyArray, lag: usize) -> PyResult<Bound<'py, PyDict>> {
    compute::growth(py, floats(&values)?, lag)
}

#[pyfunction]
fn pareto_arrow<'py>(py: Python<'py>, keys: PyArray, values: PyArray, threshold: f64) -> PyResult<Bound<'py, PyDict>> {
    compute::pareto(py, strings(&keys)?, floats(&values)?, threshold)
}

#[pyfunction]
fn ab_test_arrow<'py>(py: Python<'py>, groups: PyArray, values: PyArray, a: &str, b: &str) -> PyResult<Bound<'py, PyDict>> {
    compute::ab_test(py, strings(&groups)?, floats(&values)?, a, b)
}

#[pyfunction]
#[pyo3(signature = (ts, values, period))]
fn seasonality_arrow<'py>(py: Python<'py>, ts: PyArray, values: Option<PyArray>, period: &str) -> PyResult<Bound<'py, PyList>> {
    let values = match values {
        Some(a) => Some(floats(&a)?),
        None => None,
    };
    compute::seasonality(py, ints(&ts)?, values, period)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(describe_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(percentiles_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(histogram_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(group_stats_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(top_k_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(distinct_count_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(correlation_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(outliers_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(time_bucket_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(moving_average_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(linear_trend_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(growth_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(pareto_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(ab_test_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(seasonality_arrow, m)?)?;
    m.add("ARROW_SUPPORT", true)?;
    Ok(())
}
