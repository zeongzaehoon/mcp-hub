"""Stored results ("artifacts") on S3: the hand-off between servers. A result computed on one server is
saved under the caller's own prefix and later combined elsewhere (charts, reports) by artifact_id —
servers never call each other.

Ownership is company -> user (not key). Every read re-proves:
  * the object path is pinned to the caller's company/user (other users' objects are never addressed),
  * object metadata company_id / user_id equal the caller and the body hash matches,
  * not expired,
  * the caller may still read every server the data came from (key scope, server/binding/access active) —
    `export` instead of `read` when the data is being copied into a file or a new artifact.
Any failure is the same "not found" — existence is never disclosed."""

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import orjson
from botocore.exceptions import ClientError

from mcpcluster_common import layout
from mcpcluster_common.context import TenantContext
from mcpcluster_common.settings import CommonSettings
from mcpcluster_common.storage import ObjectStorage

Kind = Literal["table", "compute", "chart"]
ARTIFACT_ID_RE = re.compile(r"^art_[0-9a-f]{32}$")
LIST_SCAN_MAX = 500
LIST_HEAD_CONCURRENCY = 16
ServerReadable = Callable[..., Awaitable[bool]]  # (ctx, server_id, permission="read") -> bool


class ArtifactNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("artifact not found")


class ArtifactRejected(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactMeta:
    artifact_id: str
    kind: str
    company_id: str
    user_id: str
    server_id: str
    server_type: str
    key_id: str
    title: str
    created_at: str
    expires_at: str
    sha256: str
    bytes: int
    sources: tuple[str, ...] = ()  # every server the data came from (server_id plus inputs of derived artifacts)

    def public(self) -> dict[str, Any]:
        # company/user/key ids stay internal; the caller already knows who they are
        return {"artifact_id": self.artifact_id, "kind": self.kind, "title": self.title, "server_id": self.server_id,
                "server_type": self.server_type, "sources": list(self.sources), "created_at": self.created_at,
                "expires_at": self.expires_at, "bytes": self.bytes}


def _prefix(ctx: TenantContext) -> str:
    return layout.owner_prefix(layout.ARTIFACTS, ctx.company_id, ctx.user_id)


def _meta_from(obj_meta: dict[str, str]) -> ArtifactMeta:
    # S3 lower-cases user metadata keys
    m = {k.lower(): v for k, v in obj_meta.items()}
    return ArtifactMeta(
        artifact_id=m["artifact_id"], kind=m["kind"], company_id=m["company_id"], user_id=m["user_id"],
        server_id=m["server_id"], server_type=m["server_type"], key_id=m["key_id"], title=m.get("title", ""),
        created_at=m["created_at"], expires_at=m["expires_at"], sha256=m["sha256"], bytes=int(m["bytes"]),
        sources=tuple(x for x in m.get("sources", m["server_id"]).split(",") if x),
    )


def _ascii_title(title: str | None, kind: str) -> str:
    # S3 metadata must be ASCII; keep a readable, bounded label (full title lives in the body)
    t = re.sub(r"[^\x20-\x7e]", "?", (title or kind))[:120]
    return t or kind


class ArtifactStore:
    def __init__(self, settings: CommonSettings, server_readable: ServerReadable, storage: ObjectStorage | None = None):
        self.s = settings
        self.storage = storage or ObjectStorage(settings)
        self._readable = server_readable

    async def put(self, ctx: TenantContext, *, kind: Kind, title: str | None, payload: dict[str, Any],
                  derived_from: tuple[str, ...] = ()) -> ArtifactMeta:
        """`derived_from`: source servers of artifacts this one was built from (e.g. a chart of a compute result),
        so reads keep re-authorizing the server the data actually came from."""
        aid = f"art_{uuid4().hex}"
        sources = tuple(sorted({ctx.server_id, *derived_from}))
        now = datetime.now(UTC)
        body = orjson.dumps({"artifact_id": aid, "kind": kind, "title": title or "", "server_id": ctx.server_id,
                             "server_type": ctx.server_type, "sources": list(sources), "created_at": now.isoformat(), "payload": payload},
                            option=orjson.OPT_NON_STR_KEYS, default=str)
        if len(body) > self.s.artifact_max_bytes:
            raise ArtifactRejected(f"artifact is {len(body)} bytes; limit is {self.s.artifact_max_bytes} — reduce rows or aggregate first")
        meta = ArtifactMeta(
            artifact_id=aid, kind=kind, company_id=ctx.company_id, user_id=ctx.user_id, server_id=ctx.server_id,
            server_type=ctx.server_type, key_id=ctx.key_id, title=_ascii_title(title, kind), created_at=now.isoformat(),
            expires_at=(now + timedelta(days=self.s.artifact_ttl_days)).isoformat(),
            sha256=hashlib.sha256(body).hexdigest(), bytes=len(body), sources=sources,
        )
        async with self.storage.client() as c:
            await self.storage.ensure_bucket(c)
            await self.storage.put_new(c, layout.artifact_key(ctx.company_id, ctx.user_id, aid), body,
                                       ContentType="application/json",
                                       Metadata={k: (",".join(v) if isinstance(v, tuple) else str(v)) for k, v in meta.__dict__.items()})
        return meta

    async def readable(self, ctx: TenantContext, meta: ArtifactMeta, permission: str = "read") -> bool:
        for sid in meta.sources or (meta.server_id,):
            if not await self._readable(ctx, sid, permission):
                return False
        return True

    async def get(self, ctx: TenantContext, artifact_id: str, *, permission: str = "read") -> tuple[ArtifactMeta, dict[str, Any]]:
        if not isinstance(artifact_id, str) or not ARTIFACT_ID_RE.match(artifact_id):
            raise ArtifactNotFound
        async with self.storage.client() as c:
            try:
                obj = await c.get_object(Bucket=self.s.s3_bucket, Key=layout.artifact_key(ctx.company_id, ctx.user_id, artifact_id))
            except ClientError as e:
                raise ArtifactNotFound from e
            async with obj["Body"] as stream:
                body = await stream.read()
        try:
            meta = _meta_from(obj.get("Metadata") or {})
        except (KeyError, ValueError) as e:
            raise ArtifactNotFound from e
        if (meta.artifact_id != artifact_id or meta.company_id != ctx.company_id or meta.user_id != ctx.user_id
                or hashlib.sha256(body).hexdigest() != meta.sha256):
            raise ArtifactNotFound
        if datetime.fromisoformat(meta.expires_at) <= datetime.now(UTC):
            raise ArtifactNotFound
        if not await self.readable(ctx, meta, permission):
            raise ArtifactNotFound
        doc = orjson.loads(body)
        if sorted(doc.get("sources") or [meta.server_id]) != sorted(meta.sources or (meta.server_id,)):
            raise ArtifactNotFound  # provenance in metadata must match the hashed body
        return meta, doc.get("payload") or {}

    async def list(self, ctx: TenantContext, *, limit: int = 50) -> list[dict[str, Any]]:
        """Newest first, only artifacts the caller could `get` right now. Work per call is bounded: at most
        LIST_SCAN_MAX objects are examined (objects also expire after the TTL), their metadata fetched concurrently."""
        import asyncio

        now = datetime.now(UTC)
        async with self.storage.client() as c:
            await self.storage.ensure_bucket(c)
            keys: list[str] = []
            async for page in c.get_paginator("list_objects_v2").paginate(Bucket=self.s.s3_bucket, Prefix=_prefix(ctx)):
                keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".json")]
                if len(keys) >= LIST_SCAN_MAX:
                    break
            keys = keys[:LIST_SCAN_MAX]
            sem = asyncio.Semaphore(LIST_HEAD_CONCURRENCY)

            async def head(key: str) -> ArtifactMeta | None:
                async with sem:
                    try:
                        h = await c.head_object(Bucket=self.s.s3_bucket, Key=key)
                        return _meta_from(h.get("Metadata") or {})
                    except (ClientError, KeyError, ValueError):
                        return None

            metas = await asyncio.gather(*(head(k) for k in keys))
        out = [m for m in metas if m and m.company_id == ctx.company_id and m.user_id == ctx.user_id and datetime.fromisoformat(m.expires_at) > now]
        ok = {sid: await self._readable(ctx, sid) for sid in {s for m in out for s in (m.sources or (m.server_id,))}}
        out = sorted((m for m in out if all(ok[s] for s in (m.sources or (m.server_id,)))), key=lambda m: m.created_at, reverse=True)
        return [m.public() for m in out[: max(1, min(limit, 200))]]
