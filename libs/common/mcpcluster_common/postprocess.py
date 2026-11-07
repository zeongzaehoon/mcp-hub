"""Result post-processing shared by all servers: PII redaction of string cells.

Uses the Rust extension `mcpcluster_core` (PyO3 + rayon) when installed, else a pure-Python
fallback with identical semantics. Enabled per process via REDACT_PII=1 (default on outside dev).
"""

import hashlib
import os
import re
from typing import Any

try:
    import mcpcluster_core as _core

    USING_RUST = True
except ImportError:  # pragma: no cover - exercised only where the wheel is absent
    _core = None
    USING_RUST = False

_ENV = os.environ.get("REDACT_PII")
ENABLED = (_ENV == "1") if _ENV is not None else os.environ.get("APP_ENV", "") != "dev"  # unset => redact

# Keep in lockstep with rust-core/src/lib.rs (same patterns, same order).
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_BEARER = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE)
_API_KEY = re.compile(r"\b(?:mcpk_[0-9a-f]{8}_[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36}|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35})\b")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IPV6 = re.compile(r"\b(?:(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){0,5})\b")
_IPV4 = re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b")
_RRN = re.compile(r"\b\d{6}[-\s]?[1-4]\d{6}\b")
_BIZ_NO = re.compile(r"\b\d{3}-\d{2}-\d{5}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_CARD = re.compile(r"\b(?:\d{13,19}|\d{4}(?:[ -]\d{4}){2}[ -]\d{1,7}|\d{4}[ -]\d{6}[ -]\d{5})\b")
_PHONE_INTL = re.compile(r"\+\d{1,3}[-\s]?\d{1,4}[-\s]?\d{3,4}[-\s]?\d{3,4}\b")
_PHONE = re.compile(r"\b0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}\b")


def luhn_valid(s: str) -> bool:
    ds = [int(c) for c in s if c.isdigit()]
    if not 13 <= len(ds) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(ds)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def iban_valid(s: str) -> bool:
    rearranged = s[4:] + s[:4]
    rem = 0
    for c in rearranged:
        if c.isdigit():
            rem = (rem * 10 + int(c)) % 97
        elif "A" <= c <= "Z":
            rem = (rem * 100 + ord(c) - ord("A") + 10) % 97
        else:
            return False
    return rem == 1


def _py_redact(s: str) -> str:
    s = _PRIVATE_KEY.sub("[PRIVATE_KEY]", s)
    s = _JWT.sub("[JWT]", s)
    s = _AWS_KEY.sub("[AWS_KEY]", s)
    s = _BEARER.sub("Bearer [TOKEN]", s)
    s = _API_KEY.sub("[API_KEY]", s)
    s = _EMAIL.sub("[EMAIL]", s)
    s = _IPV6.sub("[IP]", s)
    s = _IPV4.sub("[IP]", s)
    s = _RRN.sub("[RRN]", s)
    s = _BIZ_NO.sub("[BIZ_NO]", s)
    s = _SSN.sub("[SSN]", s)
    s = _IBAN.sub(lambda m: "[IBAN]" if iban_valid(m.group(0)) else m.group(0), s)
    s = _PHONE_INTL.sub("[PHONE]", s)
    s = _PHONE.sub("[PHONE]", s)
    return _CARD.sub(lambda m: "[CARD]" if luhn_valid(m.group(0)) else m.group(0), s)


def redact_batch(texts: list[str | None], *, force_python: bool = False) -> list[str | None]:
    if USING_RUST and not force_python:
        return _core.redact_batch(texts)
    return [None if t is None else _py_redact(t) for t in texts]


def sha256_hex_batch(items: list[str], *, force_python: bool = False) -> list[str]:
    if USING_RUST and not force_python:
        return _core.sha256_hex_batch(items)
    return [hashlib.sha256(i.encode()).hexdigest() for i in items]


def _collect(obj: Any, out: list[str]) -> None:
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect(v, out)


def _rebuild(obj: Any, it) -> Any:
    if isinstance(obj, str):
        return next(it)
    if isinstance(obj, dict):
        return {k: _rebuild(v, it) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rebuild(v, it) for v in obj]
    return obj


def redact_rows(rows: list[dict[str, Any]], *, enabled: bool | None = None) -> list[dict[str, Any]]:
    """Redact every string cell (recursively) in one batched call."""
    if not (ENABLED if enabled is None else enabled) or not rows:
        return rows
    strings: list[str] = []
    _collect(rows, strings)
    if not strings:
        return rows
    redacted = iter(redact_batch(strings))
    return _rebuild(rows, redacted)


def redact_value(obj: Any, *, enabled: bool | None = None) -> Any:
    """Redact every string inside any JSON-like value (compute results: group keys, top-k values, …)."""
    if not (ENABLED if enabled is None else enabled):
        return obj
    strings: list[str] = []
    _collect(obj, strings)
    if not strings:
        return obj
    return _rebuild(obj, iter(redact_batch(strings)))
