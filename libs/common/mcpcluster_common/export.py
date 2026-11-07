"""File export (CSV / XLSX) of a DataFrame with two deliveries: inline base64 for small files, or an
upload to S3-compatible storage (MinIO in dev) with a short-lived presigned link for large ones.
Nothing here knows which engine produced the frame."""

import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import polars as pl

from mcpcluster_common import layout
from mcpcluster_common.context import TenantContext
from mcpcluster_common.settings import CommonSettings
from mcpcluster_common.storage import ObjectStorage

Format = Literal["csv", "xlsx"]
Delivery = Literal["auto", "inline", "link"]
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
CONTENT_TYPES = {"csv": "text/csv; charset=utf-8", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


class ExportRejected(ValueError):
    pass


_MAX_EXACT = 2**53
# Spreadsheet formula injection (CSV/XLSX): a cell starting with one of these is evaluated by Excel /
# LibreOffice / Sheets. Values come from tenant data, so any user could plant one. Plain numbers (we emit
# big/precise numbers as text, e.g. "-9007199254740993") are left untouched.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "\n")
_PLAIN_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def neutralize_formula(v: str | None) -> str | None:
    if v is None or not v.startswith(_FORMULA_START) or _PLAIN_NUMBER.match(v):
        return v
    return "'" + v


def _neutralize_formulas(df: pl.DataFrame) -> pl.DataFrame:
    """Vectorised form of neutralize_formula (exports can be 500k rows)."""
    starts = r"^[=+\-@\t\r\n]"
    exprs = [
        pl.when(pl.col(c).str.contains(starts) & ~pl.col(c).str.contains(_PLAIN_NUMBER.pattern))
        .then(pl.lit("'") + pl.col(c)).otherwise(pl.col(c)).alias(c)
        for c, t in zip(df.columns, df.dtypes, strict=True) if t == pl.Utf8
    ]
    return df.with_columns(exprs) if exprs else df


def _stringify_nested(df: pl.DataFrame) -> pl.DataFrame:
    """Lists/structs/objects cannot go into CSV/XLSX cells; JSON-encode them (never Python repr)."""
    for c, t in zip(df.columns, df.dtypes, strict=True):
        if t.is_nested() or t == pl.Object:
            df = df.with_columns(pl.Series(c, [None if v is None else json.dumps(v, default=str, ensure_ascii=False) for v in df[c].to_list()], dtype=pl.Utf8))
    return df


def _excel_safe_numbers(df: pl.DataFrame) -> pl.DataFrame:
    """Excel stores numbers as IEEE doubles: integers beyond 2^53 would be silently rounded, so such
    columns are written as text (same rule as the JSON output)."""
    for c, t in zip(df.columns, df.dtypes, strict=True):
        if t.is_integer() and df.height:
            mx = df[c].abs().max()
            if mx is not None and mx >= _MAX_EXACT:
                df = df.with_columns(pl.col(c).cast(pl.Utf8))
    return df


def to_csv_bytes(df: pl.DataFrame, *, bom: bool = True) -> bytes:
    buf = io.BytesIO()
    if bom:
        buf.write(b"\xef\xbb\xbf")  # Excel opens UTF-8 CSV correctly only with a BOM
    _neutralize_formulas(_stringify_nested(df)).write_csv(buf, datetime_format="%Y-%m-%dT%H:%M:%S%.f")
    return buf.getvalue()


def to_xlsx_bytes(df: pl.DataFrame, *, sheet: str = "data") -> bytes:
    if df.height > 1_048_575:
        raise ExportRejected("XLSX sheets hold at most 1,048,575 data rows; use csv")
    buf = io.BytesIO()
    _neutralize_formulas(_excel_safe_numbers(_stringify_nested(df))).write_excel(buf, worksheet=sheet[:31], autofit=True)
    return buf.getvalue()


def safe_filename(name: str | None, fmt: str, default_stem: str) -> str:
    stem = _SAFE_NAME.sub("_", (name or default_stem)).strip("._") or default_stem
    stem = stem.removesuffix(f".{fmt}")
    return f"{stem[:100]}.{fmt}"


@dataclass(frozen=True)
class Delivered:
    delivery: Literal["inline", "link"]
    filename: str
    size: int
    sha256: str
    content_base64: str | None = None
    url: str | None = None
    expires_at: str | None = None
    object_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d = {"delivery": self.delivery, "filename": self.filename, "bytes": self.size, "sha256": self.sha256}
        if self.content_base64 is not None:
            d["content_base64"] = self.content_base64
        if self.url is not None:
            d.update({"url": self.url, "expires_at": self.expires_at, "object_key": self.object_key})
        return d


class Exporter:
    def __init__(self, settings: CommonSettings, storage: ObjectStorage | None = None):
        self.s = settings
        self.storage = storage or ObjectStorage(settings)

    async def deliver(self, data: bytes, *, ctx: TenantContext, fmt: str, delivery: Delivery, filename: str,
                      dataset: str = layout.EXPORTS, content_type: str | None = None) -> Delivered:
        import base64

        digest = hashlib.sha256(data).hexdigest()
        mode = delivery if delivery != "auto" else ("inline" if len(data) <= self.s.export_inline_max_bytes else "link")
        if mode == "inline":
            return Delivered("inline", filename, len(data), digest, content_base64=base64.b64encode(data).decode())
        now = datetime.now(UTC)
        key = layout.file_key(dataset, ctx.company_id, ctx.user_id, now, uuid4().hex, filename)
        async with self.storage.client() as c:
            await self.storage.ensure_bucket(c)
            await self.storage.put_new(
                c, key, data, ContentType=content_type or CONTENT_TYPES[fmt],
                Metadata={"company_id": ctx.company_id, "user_id": ctx.user_id, "server_id": ctx.server_id,
                          "key_id": ctx.key_id, "sha256": digest},
            )
        # presign against the endpoint the *user* can reach; signature stays valid because the
        # client sends that same Host header
        async with self.storage.client(self.s.s3_public_endpoint) as pub:
            url = await pub.generate_presigned_url(
                "get_object", Params={"Bucket": self.s.s3_bucket, "Key": key, "ResponseContentDisposition": f'attachment; filename="{filename}"'},
                ExpiresIn=self.s.export_url_ttl_s,
            )
        return Delivered("link", filename, len(data), digest, url=url, expires_at=(now + timedelta(seconds=self.s.export_url_ttl_s)).isoformat(), object_key=key)
