//! CPU-bound helpers shared by all MCP servers. Semantics are mirrored 1:1 by the pure-Python
//! fallback in `mcpcluster_common/postprocess.py`; `tests/test_postprocess.py` asserts parity.

mod arrow_in;
mod compute;

use once_cell::sync::Lazy;
use pyo3::prelude::*;
use rayon::prelude::*;
use regex::Regex;
use sha2::{Digest, Sha256};

// ---- secrets (applied first: they may contain digits/dots that other rules would mangle)
static PRIVATE_KEY: Lazy<Regex> = Lazy::new(|| Regex::new(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----").unwrap());
static JWT: Lazy<Regex> = Lazy::new(|| Regex::new(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b").unwrap());
static AWS_KEY: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b").unwrap());
static BEARER: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}").unwrap());
// our own keys + common vendor token shapes
static API_KEY: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b(?:mcpk_[0-9a-f]{8}_[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36}|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35})\b").unwrap());
// ---- identifiers
static EMAIL: Lazy<Regex> = Lazy::new(|| Regex::new(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}").unwrap());
static IPV6: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b(?:(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,5})\b").unwrap());
static IPV4: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b").unwrap());
// Korean business registration number XXX-XX-XXXXX (hyphenated form only) and US SSN XXX-XX-XXXX
static BIZ_NO: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b\d{3}-\d{2}-\d{5}\b").unwrap());
static SSN: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b\d{3}-\d{2}-\d{4}\b").unwrap());
// IBAN: country + check digits + BBAN, validated with mod-97
static IBAN: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b").unwrap());
// international E.164-ish with separators, e.g. +82 10-1234-5678, +1 415 555 0132
static PHONE_INTL: Lazy<Regex> = Lazy::new(|| Regex::new(r"\+\d{1,3}[-\s]?\d{1,4}[-\s]?\d{3,4}[-\s]?\d{3,4}\b").unwrap());
// Korean resident registration number: YYMMDD-GNNNNNN (G in 1-4)
static RRN: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b\d{6}[-\s]?[1-4]\d{6}\b").unwrap());
// 13-19 digits: unbroken, 4-4-4-N groups, or 4-6-5 (Amex); replaced only when the Luhn checksum holds.
// Phone rules run BEFORE this one so a phone number cannot be glued onto a card number.
static CARD: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b(?:\d{13,19}|\d{4}(?:[ -]\d{4}){2}[ -]\d{1,7}|\d{4}[ -]\d{6}[ -]\d{5})\b").unwrap());
// Korean phone numbers (mobile + landline), e.g. 010-1234-5678, 02 123 4567
static PHONE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}\b").unwrap());

fn luhn(digits: &str) -> bool {
    let ds: Vec<u32> = digits.chars().filter_map(|c| c.to_digit(10)).collect();
    if ds.len() < 13 || ds.len() > 19 {
        return false;
    }
    let mut sum = 0;
    for (i, d) in ds.iter().rev().enumerate() {
        let mut v = *d;
        if i % 2 == 1 {
            v *= 2;
            if v > 9 {
                v -= 9;
            }
        }
        sum += v;
    }
    sum % 10 == 0
}

fn iban_valid(s: &str) -> bool {
    // move first 4 chars to the end, map letters A=10..Z=35, mod 97 == 1
    let rearranged = format!("{}{}", &s[4..], &s[..4]);
    let mut rem: u32 = 0;
    for c in rearranged.chars() {
        let v = if c.is_ascii_digit() { c as u32 - '0' as u32 } else if c.is_ascii_uppercase() { c as u32 - 'A' as u32 + 10 } else { return false };
        rem = if v >= 10 { (rem * 100 + v) % 97 } else { (rem * 10 + v) % 97 };
    }
    rem == 1
}

fn redact_one(s: &str) -> String {
    let s = PRIVATE_KEY.replace_all(s, "[PRIVATE_KEY]");
    let s = JWT.replace_all(&s, "[JWT]");
    let s = AWS_KEY.replace_all(&s, "[AWS_KEY]");
    let s = BEARER.replace_all(&s, "Bearer [TOKEN]");
    let s = API_KEY.replace_all(&s, "[API_KEY]");
    let s = EMAIL.replace_all(&s, "[EMAIL]");
    let s = IPV6.replace_all(&s, "[IP]");
    let s = IPV4.replace_all(&s, "[IP]");
    let s = RRN.replace_all(&s, "[RRN]");
    let s = BIZ_NO.replace_all(&s, "[BIZ_NO]");
    let s = SSN.replace_all(&s, "[SSN]");
    let s = IBAN.replace_all(&s, |caps: &regex::Captures| {
        let m = &caps[0];
        if iban_valid(m) { "[IBAN]".to_string() } else { m.to_string() }
    });
    let s = PHONE_INTL.replace_all(&s, "[PHONE]");
    let s = PHONE.replace_all(&s, "[PHONE]");
    let s = CARD.replace_all(&s, |caps: &regex::Captures| {
        let m = &caps[0];
        if luhn(m) { "[CARD]".to_string() } else { m.to_string() }
    });
    s.into_owned()
}

/// Redact PII in a batch of optional strings (None passes through). Parallel over rows.
#[pyfunction]
fn redact_batch(py: Python<'_>, texts: Vec<Option<String>>) -> Vec<Option<String>> {
    py.allow_threads(|| texts.into_par_iter().map(|t| t.map(|s| redact_one(&s))).collect())
}

#[pyfunction]
fn redact_text(s: &str) -> String {
    redact_one(s)
}

#[pyfunction]
fn luhn_valid(s: &str) -> bool {
    luhn(s)
}

#[pyfunction]
fn iban_ok(s: &str) -> bool {
    iban_valid(s)
}

/// Hex SHA-256 of each item, in parallel.
#[pyfunction]
fn sha256_hex_batch(py: Python<'_>, items: Vec<String>) -> Vec<String> {
    py.allow_threads(|| items.par_iter().map(|s| hex::encode(Sha256::digest(s.as_bytes()))).collect())
}

#[pymodule]
fn mcpcluster_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(redact_batch, m)?)?;
    m.add_function(wrap_pyfunction!(redact_text, m)?)?;
    m.add_function(wrap_pyfunction!(luhn_valid, m)?)?;
    m.add_function(wrap_pyfunction!(iban_ok, m)?)?;
    m.add_function(wrap_pyfunction!(sha256_hex_batch, m)?)?;
    compute::register(m)?;
    arrow_in::register(m)?;
    m.add("IMPLEMENTATION", "rust")?;
    Ok(())
}
