"""Console view of the artifact index: metadata of `mcp/artifacts/tenant=<cid>/...` objects, newest first. Metadata
only — the console never gets the payload or a download link; data leaves through the MCP `export` path. Needs
ListBucket (prefix mcp/artifacts/) + GetObject-for-HEAD on the backend role (infra/terraform/iam.tf)."""

import asyncio
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError

from app.core.config import Settings
from app.infra.s3.audit import s3_client

PREFIX = "mcp/artifacts"
SCAN_MAX = 500
HEAD_CONCURRENCY = 8
PUBLIC = ("artifact_id", "kind", "title", "server_id", "server_type", "created_at", "expires_at")


def _public(m: dict[str, str], key: str) -> dict[str, Any] | None:
    m = {k.lower(): v for k, v in m.items()}
    try:
        out: dict[str, Any] = {f: m.get(f, "") for f in PUBLIC}
        out["user_id"] = m["user_id"]
        out["bytes"] = int(m.get("bytes") or 0)
        out["sources"] = [x for x in m.get("sources", m.get("server_id", "")).split(",") if x]
        out["key"] = key
        return out
    except (KeyError, ValueError):
        return None


class ArtifactIndex:
    def __init__(self, settings: Settings):
        self.s = settings

    async def list_for_company(self, company_id: str, *, user_id: str | None = None, limit: int = 50, include_expired: bool = False) -> list[dict[str, Any]]:
        if "/" in company_id or "=" in company_id or (user_id and ("/" in user_id or "=" in user_id)):
            return []
        prefix = f"{PREFIX}/tenant={company_id}/" + (f"user={user_id}/" if user_id else "")
        limit = max(1, min(limit, 100))
        now = datetime.now(UTC)
        async with s3_client(self.s) as c:
            objs: list[tuple[str, datetime]] = []
            try:
                async for page in c.get_paginator("list_objects_v2").paginate(Bucket=self.s.s3_bucket, Prefix=prefix):
                    objs += [(o["Key"], o["LastModified"]) for o in page.get("Contents", []) if o["Key"].endswith(".json")]
                    if len(objs) >= SCAN_MAX:
                        break
            except ClientError as e:
                if e.response["Error"]["Code"] in ("NoSuchBucket", "404"):
                    return []
                raise
            objs.sort(key=lambda x: x[1], reverse=True)
            sem = asyncio.Semaphore(HEAD_CONCURRENCY)

            async def head(key: str) -> dict[str, Any] | None:
                async with sem:
                    try:
                        h = await c.head_object(Bucket=self.s.s3_bucket, Key=key)
                    except ClientError:
                        return None
                    return _public(h.get("Metadata") or {}, key)

            out: list[dict[str, Any]] = []
            pos = 0
            while len(out) < limit and pos < len(objs):
                chunk = objs[pos: pos + limit * 2]
                pos += len(chunk)
                for m in await asyncio.gather(*(head(k) for k, _ in chunk)):
                    if not m:
                        continue
                    try:
                        expired = datetime.fromisoformat(m["expires_at"]) <= now
                    except ValueError:
                        expired = False
                    if expired and not include_expired:
                        continue
                    m["expired"] = expired
                    out.append(m)
        return out[:limit]
