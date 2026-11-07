"""One-shot move of persistent objects from the pre-2026-10 layout (bucket `mcp-exports`, roots `monitors/`,
`monitor-state/`) into `ftg-mcp` (layout.py). Only monitors move: `artifacts/` `exports/` `reports/` are short-lived
TTL output and simply expire in the old bucket.

    python -m mcpcluster_common.migrate_layout [--source-bucket mcp-exports] [--execute] [--delete-legacy]

Default is a dry run. Per object: read -> check the path agrees with the document's own company/user/monitor ids
(a mismatch is skipped, never "fixed") -> write-once to the new key -> read back and compare sha256 -> optionally
delete the source. A target that already holds the same bytes only cleans the source; different bytes are skipped.
Idempotent: re-running after a partial run finishes the rest."""

import argparse
import asyncio
import hashlib
import re
import sys

import orjson
from botocore.exceptions import ClientError

from mcpcluster_common import layout
from mcpcluster_common.settings import CommonSettings
from mcpcluster_common.storage import ObjectStorage

_DEF = re.compile(r"^monitors/(?P<st>[^/]+)/(?P<cid>[^/]+)/(?P<uid>[^/]+)/(?P<mid>[^/]+)\.json$")
_STATE = re.compile(r"^monitor-state/(?P<cid>[^/]+)/(?P<uid>[^/]+)/(?P<mid>[^/]+)\.json$")


def plan_key(old: str, doc: dict) -> str | None:
    """New key for a legacy object, or None when the path and the document disagree (or ids are unusable)."""
    try:
        if m := _DEF.match(old):
            ok = (doc.get("server_type"), doc.get("company_id"), doc.get("user_id"), doc.get("monitor_id")) == (m["st"], m["cid"], m["uid"], m["mid"])
            return layout.monitor_key(m["st"], m["cid"], m["uid"], m["mid"]) if ok else None
        if m := _STATE.match(old):
            return layout.monitor_state_key(m["cid"], m["uid"], m["mid"])  # state docs carry no ids; the path is all we have
    except layout.LayoutError:
        return None
    return None


async def _read(c, bucket: str, key: str) -> bytes | None:
    try:
        obj = await c.get_object(Bucket=bucket, Key=key)
    except ClientError:
        return None
    async with obj["Body"] as b:
        return await b.read()


async def migrate(s: CommonSettings, source: str, *, execute: bool, delete_legacy: bool) -> dict[str, int]:
    st = ObjectStorage(s)
    out = {"moved": 0, "already": 0, "conflict": 0, "skipped": 0, "deleted": 0, "planned": 0}
    async with st.client() as c:
        if execute:
            await st.ensure_bucket(c)
        for root in ("monitors/", "monitor-state/"):
            async for page in c.get_paginator("list_objects_v2").paginate(Bucket=source, Prefix=root):
                for o in page.get("Contents", []):
                    old = o["Key"]
                    body = await _read(c, source, old)
                    try:
                        doc = orjson.loads(body) if body is not None else None
                    except orjson.JSONDecodeError:
                        doc = None
                    new = plan_key(old, doc) if isinstance(doc, dict) else None
                    if new is None:
                        out["skipped"] += 1
                        print(f"skip      {old} (path/document mismatch or unreadable)")
                        continue
                    if not execute:
                        out["planned"] += 1
                        print(f"plan      {old} -> {new}")
                        continue
                    digest = hashlib.sha256(body).hexdigest()
                    cur = await _read(c, s.s3_bucket, new)
                    if cur is None:
                        await st.put_new(c, new, body, ContentType="application/json")
                        cur = await _read(c, s.s3_bucket, new)
                        state = "moved"
                    else:
                        state = "already"
                    if cur is None or hashlib.sha256(cur).hexdigest() != digest:
                        out["conflict"] += 1
                        print(f"conflict  {old} -> {new} (target differs; source kept)")
                        continue
                    out[state] += 1
                    print(f"{state:<9} {old} -> {new}")
                    if delete_legacy:
                        await c.delete_object(Bucket=source, Key=old)
                        out["deleted"] += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source-bucket", default="mcp-exports")
    ap.add_argument("--execute", action="store_true", help="copy (default: dry run)")
    ap.add_argument("--delete-legacy", action="store_true", help="with --execute: delete each source once verified")
    a = ap.parse_args()
    s = CommonSettings()
    if a.source_bucket == s.s3_bucket:
        sys.exit("source and target bucket are the same")
    res = asyncio.run(migrate(s, a.source_bucket, execute=a.execute, delete_legacy=a.execute and a.delete_legacy))
    print(" ".join(f"{k}={v}" for k, v in res.items()))
    sys.exit(1 if res["conflict"] else 0)


if __name__ == "__main__":
    main()
