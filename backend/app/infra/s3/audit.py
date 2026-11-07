"""Access-log lake: `audit/access_logs/tenant=<company_id>/dt=<YYYYMMDD>/<first>_<last>.parquet` in the `ftg-mcp`
bucket (contract: docs/S3_LAYOUT.md; MCP-side keys: libs/common/mcpcluster_common/layout.py).

DynamoDB keeps the last 90 days for the console; this is the >= 1 year record (개인정보의 안전성 확보조치 기준 §8).
Append-only: one file per consumed batch and (tenant, UTC day), named by the Redis stream ids it holds, written with
`If-None-Match: *` — a replayed batch hits the same key and is a no-op, nothing is ever overwritten. Partition columns
(tenant, dt) live in the path, not in the file. The IAM role may only PutObject under audit/ (no read, no delete)."""

import io
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import aioboto3
import orjson
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import Settings

PREFIX = "audit/access_logs"
SCHEMA_VERSION = 1
_PART = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SCHEMA = pa.schema([
    ("schema_version", pa.int32()), ("stream_id", pa.string()), ("ts", pa.string()), ("key_id", pa.string()),
    ("user_id", pa.string()), ("server_id", pa.string()), ("server_type", pa.string()), ("tool", pa.string()),
    ("args_summary", pa.string()), ("status", pa.string()), ("error", pa.string()), ("duration_ms", pa.int64()),
    ("extra_json", pa.string()),
])


def tenant_of(ev: dict[str, Any]) -> str:
    # an unauthenticated call has no (valid) company: `_` like the data-engineering lake, never a path-changing value
    cid = ev.get("company_id")
    return cid if isinstance(cid, str) and _PART.match(cid) and cid != "unknown" else "_"


def day_of(ev: dict[str, Any]) -> str:
    try:
        return datetime.fromisoformat(ev["ts"]).astimezone(UTC).strftime("%Y%m%d")
    except (KeyError, TypeError, ValueError):
        return "_"


def key_for(tenant: str, day: str, stream_ids: list[str]) -> str:
    return f"{PREFIX}/tenant={tenant}/dt={day}/{stream_ids[0]}_{stream_ids[-1]}.parquet"


def _row(sid: str, ev: dict[str, Any]) -> dict[str, Any]:
    def s(k: str) -> str | None:
        v = ev.get(k)
        return None if v is None else str(v)

    try:
        dur = int(ev.get("duration_ms") or 0)
    except (TypeError, ValueError):
        dur = 0
    return {"schema_version": SCHEMA_VERSION, "stream_id": sid, "ts": s("ts"), "key_id": s("key_id"), "user_id": s("user_id"),
            "server_id": s("server_id"), "server_type": s("server_type"), "tool": s("tool"), "args_summary": s("args_summary"),
            "status": s("status"), "error": s("error"), "duration_ms": dur,
            "extra_json": orjson.dumps(ev.get("extra") or {}, default=str).decode()}


def to_parquet(rows: list[dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), buf, compression="zstd")
    return buf.getvalue()


def partition(events: list[tuple[str, dict[str, Any]]]) -> dict[str, bytes]:
    """(stream_id, event) in stream order -> {key: parquet bytes}, one file per (tenant, day)."""
    groups: dict[tuple[str, str], list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for sid, ev in events:
        groups[(tenant_of(ev), day_of(ev))].append((sid, ev))
    return {key_for(t, d, [sid for sid, _ in g]): to_parquet([_row(sid, ev) for sid, ev in g]) for (t, d), g in groups.items()}


_SESSION = aioboto3.Session()


def s3_client(settings: Settings):
    """One S3 client factory for the backend (audit lake writes, artifact index reads)."""
    creds = {}
    if settings.s3_access_key:  # dev MinIO; prod uses the task role (default credential chain)
        creds = {"aws_access_key_id": settings.s3_access_key, "aws_secret_access_key": settings.s3_secret_key}
    return _SESSION.client("s3", endpoint_url=settings.s3_endpoint or None, region_name=settings.aws_region, **creds,
                           config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))


class AuditLake:
    def __init__(self, settings: Settings):
        self.s = settings
        self._bucket_ready = False

    def _client(self):
        return s3_client(self.s)

    async def write(self, events: list[tuple[str, dict[str, Any]]]) -> int:
        files = partition(events)
        if not files:
            return 0
        async with self._client() as c:
            if self.s.s3_manage_bucket and not self._bucket_ready:  # dev only; lifecycle is set by the MCP side
                try:
                    await c.head_bucket(Bucket=self.s.s3_bucket)
                except ClientError:
                    try:
                        await c.create_bucket(Bucket=self.s.s3_bucket, CreateBucketConfiguration={"LocationConstraint": self.s.aws_region})
                    except ClientError as e:
                        if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                            raise
                self._bucket_ready = True
            for key, body in files.items():
                try:
                    await c.put_object(Bucket=self.s.s3_bucket, Key=key, Body=body, IfNoneMatch="*",
                                       ContentType="application/vnd.apache.parquet")
                except ClientError as e:
                    if e.response["Error"]["Code"] != "PreconditionFailed":
                        raise  # 412 = this batch is already in the lake
        return len(files)
