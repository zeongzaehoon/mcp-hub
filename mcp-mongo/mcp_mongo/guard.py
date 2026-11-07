"""Admission control for MongoDB operations.

Isolation comes from the connection: every operation runs on `client[tenant.database]`, and MongoDB
has no cross-database reads inside find/aggregate. What remains to block:
  * server-side JavaScript: $where, $function, $accumulator (arbitrary code, cross-db via db.getSiblingDB)
  * aggregation stages that write or escape the collection: $out, $merge (can target other databases),
    $currentOp, $listSessions, $listLocalSessions, $planCacheStats, $collStats, $indexStats
  * system collections (`system.*`) and invalid collection names
  * update documents that are full replacements (only operator updates allowed) and
    empty filters on update/delete unless explicitly acknowledged
"""

import re
from typing import Any

# MongoDB's own rules, nothing stricter: non-empty, no NUL, no "$", not reserved (system.*), <= 120 bytes
COLLECTION = re.compile(r"^[^\x00$]{1,120}$")
FORBIDDEN_OPERATORS = {"$where", "$function", "$accumulator"}
FORBIDDEN_STAGES = {
    "$out", "$merge", "$currentOp", "$listSessions", "$listLocalSessions", "$planCacheStats",
    "$collStats", "$indexStats", "$listSampledQueries", "$listSearchIndexes", "$querySettings", "$shardedDataDistribution",
}
MAX_PIPELINE_STAGES = 50
MAX_DEPTH = 32


class MongoRejected(ValueError):
    pass


def check_collection(name: str) -> str:
    if not COLLECTION.match(name) or name.startswith("system."):
        raise MongoRejected(f"invalid or reserved collection name: {name!r}")
    return name


def _walk(obj: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise MongoRejected("document nesting too deep")
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_OPERATORS:
                raise MongoRejected(f"operator {k} is not allowed (server-side JavaScript)")
            _walk(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, depth + 1)


def check_filter(f: Any, *, allow_empty: bool = True) -> dict[str, Any]:
    if f is None:
        f = {}
    if not isinstance(f, dict):
        raise MongoRejected("filter must be an object")
    if not f and not allow_empty:
        raise MongoRejected("empty filter would affect every document; pass allow_empty_filter=true to confirm")
    _walk(f)
    return f


def check_projection(p: Any) -> dict[str, Any] | None:
    if p is None:
        return None
    if not isinstance(p, dict):
        raise MongoRejected("projection must be an object")
    _walk(p)
    return p


def check_pipeline(stages: Any) -> list[dict[str, Any]]:
    if not isinstance(stages, list) or not stages:
        raise MongoRejected("pipeline must be a non-empty array of stages")
    if len(stages) > MAX_PIPELINE_STAGES:
        raise MongoRejected("pipeline too long")
    for st in stages:
        if not isinstance(st, dict) or len(st) != 1:
            raise MongoRejected("each stage must be an object with exactly one $stage key")
        (name,) = st.keys()
        if not name.startswith("$"):
            raise MongoRejected(f"stage {name!r} must start with $")
        if name in FORBIDDEN_STAGES:
            raise MongoRejected(f"stage {name} is not allowed")
        if name in ("$lookup", "$graphLookup", "$unionWith"):
            spec = st[name]
            target = spec.get("from") if isinstance(spec, dict) else spec
            if name == "$unionWith" and isinstance(spec, dict):
                target = spec.get("coll")
            if isinstance(target, str):
                check_collection(target)
            elif target is not None:
                raise MongoRejected(f"{name} target must be a collection name")
        _walk(st)
    return stages


def check_update(u: Any) -> dict[str, Any] | list[dict[str, Any]]:
    if isinstance(u, list):  # pipeline-style update
        return check_pipeline(u)
    if not isinstance(u, dict) or not u:
        raise MongoRejected("update must be a non-empty object")
    if not all(k.startswith("$") for k in u):
        raise MongoRejected("update must use update operators ($set, $inc, …); full replacement is not allowed")
    _walk(u)
    return u


def check_documents(docs: Any, max_docs: int) -> list[dict[str, Any]]:
    if not isinstance(docs, list) or not docs:
        raise MongoRejected("documents must be a non-empty array")
    if len(docs) > max_docs:
        raise MongoRejected(f"at most {max_docs} documents per call")
    for d in docs:
        if not isinstance(d, dict):
            raise MongoRejected("each document must be an object")
        _walk(d)
    return docs
