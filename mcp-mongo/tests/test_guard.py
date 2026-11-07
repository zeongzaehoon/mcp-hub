import pytest

from mcp_mongo import guard
from mcp_mongo.guard import MongoRejected


@pytest.mark.parametrize("name", ["system.users", "system.js", "a$b", "", "a\x00b", "x" * 130])
def test_bad_collections(name):
    with pytest.raises(MongoRejected):
        guard.check_collection(name)


@pytest.mark.parametrize("name", ["customers.v2-archive", "Mixed Case", "1abc", "한글컬렉션", "V iew"])
def test_good_collections(name):
    """Only MongoDB's own naming rules apply — no assumptions about how a datasource names things."""
    assert guard.check_collection(name) == name


@pytest.mark.parametrize("f", [
    {"$where": "this.a == 1"},
    {"a": {"$in": [1, {"$where": "1"}]}},
    {"$expr": {"$function": {"body": "function(){}", "args": [], "lang": "js"}}},
])
def test_filter_rejects_js(f):
    with pytest.raises(MongoRejected):
        guard.check_filter(f)


def test_empty_filter_gate():
    assert guard.check_filter({}) == {}
    with pytest.raises(MongoRejected):
        guard.check_filter({}, allow_empty=False)
    with pytest.raises(MongoRejected):
        guard.check_filter(None, allow_empty=False)


@pytest.mark.parametrize("p", [
    [{"$match": {}}, {"$out": "other"}],
    [{"$merge": {"into": {"db": "globex_mg", "coll": "customers"}}}],
    [{"$currentOp": {}}],
    [{"$group": {"_id": None, "x": {"$accumulator": {"init": "function(){}", "accumulate": "function(){}", "accumulateArgs": [], "merge": "function(){}", "lang": "js"}}}}],
    [{"$lookup": {"from": "system.users", "localField": "a", "foreignField": "b", "as": "u"}}],
    [{"$unionWith": "system.views"}],
    [{"$match": {}, "$limit": 1}],
    [{"match": {}}],
    [],
    "not a list",
])
def test_pipeline_rejects(p):
    with pytest.raises(MongoRejected):
        guard.check_pipeline(p)


def test_pipeline_ok():
    p = [{"$match": {"plan": "pro"}}, {"$lookup": {"from": "events", "localField": "_id", "foreignField": "cid", "as": "ev"}}, {"$group": {"_id": "$plan", "n": {"$sum": 1}}}]
    assert guard.check_pipeline(p) == p


def test_update_rules():
    assert guard.check_update({"$set": {"a": 1}}) == {"$set": {"a": 1}}
    assert guard.check_update([{"$set": {"a": 1}}]) == [{"$set": {"a": 1}}]
    with pytest.raises(MongoRejected):
        guard.check_update({"a": 1})  # full replacement
    with pytest.raises(MongoRejected):
        guard.check_update({})
    with pytest.raises(MongoRejected):
        guard.check_update({"$set": {"a": {"$where": "1"}}})


def test_documents_rules():
    with pytest.raises(MongoRejected):
        guard.check_documents([], 10)
    with pytest.raises(MongoRejected):
        guard.check_documents([{}] * 11, 10)
    with pytest.raises(MongoRejected):
        guard.check_documents([{"f": {"$function": {}}}], 10)
    assert guard.check_documents([{"a": 1}], 10) == [{"a": 1}]


def test_depth_limit():
    deep: dict = {}
    cur = deep
    for _ in range(40):
        cur["x"] = {}
        cur = cur["x"]
    with pytest.raises(MongoRejected):
        guard.check_filter(deep)
