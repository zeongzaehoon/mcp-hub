import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from bson import json_util
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pymongo.errors import PyMongoError

from mcp_mongo import guard
from mcp_mongo.client import MAX_INSERT, MAX_ROWS, MAX_TIME_MS, SETTINGS, registry
from mcpcluster_common import TenantAuthMiddleware, current_tenant
from mcpcluster_common.monitor import start_background, stop_background
from mcpcluster_common.datatools import TOOL_PERMISSIONS as DATA_TOOLS, install_data_tools
from mcpcluster_common.postprocess import redact_rows
from mcpcluster_common.serialize import to_arrow_scalar

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mcp-mongo")

# Must equal backend/app/domain/tool_catalog.py[ServerType.MONGO]
TOOL_PERMISSIONS: dict[str, str] = {
    "list_collections": "read",
    "find": "read",
    "aggregate": "read",
    "insert_many": "create",
    "update_many": "update",
    "delete_many": "delete",
    **DATA_TOOLS,  # transform (read) + export (export) — engine-agnostic, from mcpcluster_common
}


@asynccontextmanager
async def lifespan(_app):
    start_background()  # monitor scheduler
    try:
        yield
    finally:
        await stop_background()
        await registry.close_all()


mcp = FastMCP("mcp-mongo", instructions="Tenant-scoped MongoDB access (one database per tenant). Every call is authorized by API key.", lifespan=lifespan)


def _bson_in(obj: Any) -> Any:
    """Accept MongoDB Extended JSON in inputs ({"$oid": ...}, {"$date": ...})."""
    return json_util.loads(json.dumps(obj)) if obj is not None else None


def _json_out(obj: Any) -> Any:
    out = json.loads(json_util.dumps(obj, json_options=json_util.RELAXED_JSON_OPTIONS))
    return redact_rows(out) if isinstance(out, list) and out and isinstance(out[0], dict) else out


async def _db():
    ctx = current_tenant()
    if ctx.tenant.mode != "database":
        raise ToolError(f"tenant mode {ctx.tenant.mode} is not supported by mcp-mongo")
    client = await registry.get(ctx)
    return client[ctx.tenant.database]


def _rejected(e: Exception) -> ToolError:
    return ToolError(f"rejected: {e}")


@mcp.tool
async def list_collections() -> dict[str, Any]:
    """Collections in the tenant database."""
    db = await _db()
    try:
        infos = await db.list_collections(filter={"name": {"$not": {"$regex": "^system\\."}}})
        rows = [{"name": i["name"], "type": i.get("type", "collection")} for i in await infos.to_list(length=None)]
    except PyMongoError as e:
        raise ToolError(f"mongo error: {e}") from e
    return {"rows": sorted(rows, key=lambda r: r["name"]), "row_count": len(rows)}


@mcp.tool
async def find(collection: str, filter: dict[str, Any] | None = None, projection: dict[str, Any] | None = None, sort: dict[str, int] | None = None, limit: int = 100, skip: int = 0) -> dict[str, Any]:
    """Query documents. filter/projection accept Extended JSON. limit is capped server-side."""
    db = await _db()
    try:
        coll = guard.check_collection(collection)
        flt = _bson_in(guard.check_filter(filter))
        proj = guard.check_projection(projection)
    except guard.MongoRejected as e:
        raise _rejected(e) from e
    lim = max(1, min(int(limit), MAX_ROWS))
    try:
        cur = db[coll].find(flt, proj, skip=max(0, int(skip)), limit=lim + 1, max_time_ms=MAX_TIME_MS)
        if sort:
            cur = cur.sort(list(sort.items()))
        docs = await cur.to_list(length=lim + 1)
    except PyMongoError as e:
        raise ToolError(f"mongo error: {e}") from e
    return {"rows": _json_out(docs[:lim]), "row_count": min(len(docs), lim), "truncated": len(docs) > lim}


@mcp.tool
async def aggregate(collection: str, pipeline: list[dict[str, Any]], limit: int = 1000) -> dict[str, Any]:
    """Run an aggregation pipeline (read-only: $out/$merge and JS stages are refused)."""
    db = await _db()
    try:
        coll = guard.check_collection(collection)
        stages = _bson_in(guard.check_pipeline(pipeline))
    except guard.MongoRejected as e:
        raise _rejected(e) from e
    lim = max(1, min(int(limit), MAX_ROWS))
    try:
        cur = await db[coll].aggregate([*stages, {"$limit": lim + 1}], maxTimeMS=MAX_TIME_MS, allowDiskUse=False)
        docs = await cur.to_list(length=lim + 1)
    except PyMongoError as e:
        raise ToolError(f"mongo error: {e}") from e
    return {"rows": _json_out(docs[:lim]), "row_count": min(len(docs), lim), "truncated": len(docs) > lim}


@mcp.tool
async def insert_many(collection: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Insert documents into a tenant collection."""
    db = await _db()
    try:
        coll = guard.check_collection(collection)
        docs = _bson_in(guard.check_documents(documents, MAX_INSERT))
    except guard.MongoRejected as e:
        raise _rejected(e) from e
    try:
        res = await db[coll].insert_many(docs, ordered=True)
    except PyMongoError as e:
        raise ToolError(f"mongo error: {e}") from e
    return {"inserted_count": len(res.inserted_ids), "inserted_ids": _json_out(res.inserted_ids)}


@mcp.tool
async def update_many(collection: str, filter: dict[str, Any], update: dict[str, Any] | list[dict[str, Any]], upsert: bool = False, allow_empty_filter: bool = False) -> dict[str, Any]:
    """Update documents with operator updates ($set, $inc, …). Empty filter needs allow_empty_filter=true."""
    db = await _db()
    try:
        coll = guard.check_collection(collection)
        flt = _bson_in(guard.check_filter(filter, allow_empty=allow_empty_filter))
        upd = _bson_in(guard.check_update(update))
    except guard.MongoRejected as e:
        raise _rejected(e) from e
    try:
        res = await db[coll].update_many(flt, upd, upsert=upsert)
    except PyMongoError as e:
        raise ToolError(f"mongo error: {e}") from e
    return {"matched_count": res.matched_count, "modified_count": res.modified_count, "upserted_id": _json_out(res.upserted_id)}


@mcp.tool
async def delete_many(collection: str, filter: dict[str, Any], allow_empty_filter: bool = False) -> dict[str, Any]:
    """Delete documents matching filter. Empty filter needs allow_empty_filter=true."""
    db = await _db()
    try:
        coll = guard.check_collection(collection)
        flt = _bson_in(guard.check_filter(filter, allow_empty=allow_empty_filter))
    except guard.MongoRejected as e:
        raise _rejected(e) from e
    try:
        res = await db[coll].delete_many(flt)
    except PyMongoError as e:
        raise ToolError(f"mongo error: {e}") from e
    return {"deleted_count": res.deleted_count}


def _tabular(doc: dict[str, Any]) -> dict[str, Any]:
    """Documents -> flat rows without assuming a schema: scalars stay typed (Decimal128 -> Decimal,
    datetime kept, ObjectId -> str); nested docs/arrays/binary become JSON strings so they fit a cell."""
    out: dict[str, Any] = {}
    for k, v in doc.items():
        tname = type(v).__name__
        if tname == "Decimal128":
            v = v.to_decimal()
        elif tname == "ObjectId":
            v = str(v)
        elif isinstance(v, (dict, list)) or tname in ("Binary", "Regex", "Timestamp", "Code", "DBRef", "MinKey", "MaxKey"):
            v = json_util.dumps(v, json_options=json_util.RELAXED_JSON_OPTIONS)
        out[str(k)] = to_arrow_scalar(v)
    return out


async def _fetch_source(source: dict[str, Any], max_rows: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`source` = {"collection": "...", "filter"?: {...}, "projection"?: {...}, "sort"?: {...}} or
    {"collection": "...", "pipeline": [...]}; the same guards as find/aggregate apply."""
    if not isinstance(source, dict) or "collection" not in source:
        raise ToolError("rejected: source needs a collection")
    db = await _db()
    try:
        coll = guard.check_collection(str(source["collection"]))
        if "pipeline" in source:
            stages = _bson_in(guard.check_pipeline(source["pipeline"]))
            cur = await db[coll].aggregate([*stages, {"$limit": max_rows + 1}], maxTimeMS=MAX_TIME_MS, allowDiskUse=False)
        else:
            flt = _bson_in(guard.check_filter(source.get("filter")))
            proj = guard.check_projection(source.get("projection"))
            cur = db[coll].find(flt, proj, limit=max_rows + 1, max_time_ms=MAX_TIME_MS)
            if source.get("sort"):
                cur = cur.sort(list(dict(source["sort"]).items()))
        docs = await cur.to_list(length=max_rows + 1)
    except guard.MongoRejected as e:
        raise _rejected(e) from e
    except PyMongoError as e:
        raise ToolError(f"mongo error: {e}") from e
    return [_tabular(d) for d in docs[:max_rows]], {"collection": coll, "source_truncated": len(docs) > max_rows}


install_data_tools(mcp, _fetch_source, SETTINGS, source_doc='{"collection": "...", "filter"?: {...}, "projection"?: {...}, "sort"?: {...}} or {"collection": "...", "pipeline": [...]}')


def install_auth(server: FastMCP) -> FastMCP:
    server.add_middleware(TenantAuthMiddleware(SETTINGS, TOOL_PERMISSIONS))
    return server


def main() -> None:
    SETTINGS.ensure_safe_runtime()
    from mcpcluster_common.tracing import setup_tracing

    setup_tracing("mcp-mongo")
    install_auth(mcp)
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "9003")), path="/mcp", stateless_http=True)


if __name__ == "__main__":
    main()
