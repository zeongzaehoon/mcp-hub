"""Engine-agnostic result serialisation. Any driver value becomes JSON-safe without knowing the schema:
numbers stay numbers when exact, everything exotic degrades to a faithful string, bytes to base64.
Never raises on an unknown type."""

import base64
import datetime as dt
import json
import ipaddress
import math
import uuid
from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
from typing import Any

_MAX_EXACT_INT = 2**53


def to_jsonable(v: Any) -> Any:  # noqa: PLR0911 - a flat type switch is the clearest form here
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        return v if abs(v) < _MAX_EXACT_INT else str(v)
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, Decimal):
        if v.is_finite() and v == v.to_integral_value() and abs(v) < _MAX_EXACT_INT:
            return int(v)
        return str(v)  # keep precision; LLM-facing clients handle numeric strings
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"$base64": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, (ipaddress.IPv4Address, ipaddress.IPv6Address, ipaddress.IPv4Network, ipaddress.IPv6Network, ipaddress.IPv4Interface, ipaddress.IPv6Interface)):
        return str(v)
    if isinstance(v, Enum):
        return to_jsonable(v.value)
    if isinstance(v, Mapping):
        return {str(k): to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [to_jsonable(x) for x in v]
    tname = type(v).__name__
    if tname == "Record" and hasattr(v, "items"):  # asyncpg.Record (not a Mapping)
        return {str(k): to_jsonable(x) for k, x in v.items()}
    if tname == "Range" and hasattr(v, "lower") and hasattr(v, "upper"):  # asyncpg.Range
        if getattr(v, "isempty", False):
            return {"empty": True}
        return {"lower": to_jsonable(v.lower), "upper": to_jsonable(v.upper), "lower_inc": v.lower_inc, "upper_inc": v.upper_inc}
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:  # noqa: BLE001
            pass
    return str(v)


def rows_from_records(records) -> list[dict[str, Any]]:
    """asyncpg / DB-API style records with .keys()/.values() or plain mappings -> list of JSON-safe dicts."""
    out: list[dict[str, Any]] = []
    for r in records:
        if isinstance(r, Mapping):
            out.append({str(k): to_jsonable(x) for k, x in r.items()})
        elif hasattr(r, "items"):
            out.append({str(k): to_jsonable(x) for k, x in r.items()})
        else:
            out.append({"value": to_jsonable(r)})
    return out


def rows_from_columns(column_names, result_rows) -> list[dict[str, Any]]:
    names = [str(c) for c in column_names]
    return [{n: to_jsonable(x) for n, x in zip(names, row, strict=False)} for row in result_rows]


def to_arrow_scalar(v: Any) -> Any:
    """Keep what Arrow infers well (numbers, Decimal, bool, str, datetime/date, None); degrade the rest
    through to_jsonable so pa.Table.from_pylist never fails on an unknown driver type."""
    if v is None or isinstance(v, (bool, int, float, str, Decimal, dt.datetime, dt.date)):
        return v
    out = to_jsonable(v)
    if out is None or isinstance(out, (str, int, float, bool)):
        return out
    return json.dumps(out, ensure_ascii=False)  # nested values become JSON text, never Python repr


def arrow_rows_from_columns(column_names, result_rows) -> list[dict[str, Any]]:
    names = [str(c) for c in column_names]
    return [{n: to_arrow_scalar(x) for n, x in zip(names, row, strict=False)} for row in result_rows]


def arrow_rows_from_records(records) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in records:
        items = r.items() if hasattr(r, "items") else [("value", r)]
        out.append({str(k): to_arrow_scalar(x) for k, x in items})
    return out
