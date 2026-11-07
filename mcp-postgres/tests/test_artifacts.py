"""Artifact store against the aws-local MinIO (localhost:9000). Skipped when it is not reachable."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import orjson
import pytest

from mcpcluster_common import layout
from mcpcluster_common.artifacts import ArtifactNotFound, ArtifactRejected, ArtifactStore
from mcpcluster_common.context import TenantContext
from mcpcluster_common.model import TenantSpec
from mcpcluster_common.settings import CommonSettings

ENDPOINT = os.environ.get("TEST_S3_ENDPOINT", "http://localhost:9000")


def _up() -> bool:
    try:
        return httpx.get(f"{ENDPOINT}/minio/health/live", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _up(), reason="object storage not reachable")
BUCKET = f"test-artifacts-{uuid.uuid4().hex[:8]}"


def _ctx(company="acme", user="u1", server="s1", scope=None, perms=("read", "export")) -> TenantContext:
    return TenantContext(company_id=company, user_id=user, key_id="k", server_id=server, server_type="postgres",
                         permissions=frozenset(perms), allowed_tools=frozenset({"compute"}),
                         tenant=TenantSpec(mode="schema", schema_name="a"), binding_version=1, key_scope=scope)


class Readable:
    """Stands in for Resolver.server_readable: servers the caller may currently read."""

    def __init__(self, ok=("s1", "s2")):
        self.ok = set(ok)

    async def __call__(self, ctx, sid, permission="read"):
        return sid in self.ok and (ctx.key_scope is None or sid in ctx.key_scope)


@pytest.fixture(scope="module", autouse=True)
async def _drop_bucket():
    yield
    st = ArtifactStore(CommonSettings(s3_endpoint=ENDPOINT, s3_bucket=BUCKET), Readable())
    async with st.storage.client() as c:
        try:
            listed = await c.list_objects_v2(Bucket=BUCKET)
            for o in listed.get("Contents", []):
                await c.delete_object(Bucket=BUCKET, Key=o["Key"])
            await c.delete_bucket(Bucket=BUCKET)
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup
            pass


@pytest.fixture
def store():
    s = CommonSettings(s3_endpoint=ENDPOINT, s3_public_endpoint=ENDPOINT, s3_bucket=BUCKET)
    return ArtifactStore(s, Readable())


async def test_roundtrip_and_listing(store):
    ctx = _ctx()
    m = await store.put(ctx, kind="compute", title="매출 합계 (Q3)", payload={"result": {"sum": 12345.5}})
    assert m.artifact_id.startswith("art_") and len(m.artifact_id) == 36
    meta, payload = await store.get(ctx, m.artifact_id)
    assert payload == {"result": {"sum": 12345.5}} and meta.server_id == "s1"
    listed = await store.list(ctx)
    assert listed[0]["artifact_id"] == m.artifact_id and "company_id" not in listed[0]


async def test_other_user_and_other_company_see_nothing(store):
    m = await store.put(_ctx(), kind="table", title="t", payload={"rows": [{"a": 1}]})
    for other in (_ctx(user="u2"), _ctx(company="globex")):
        with pytest.raises(ArtifactNotFound):
            await store.get(other, m.artifact_id)
        assert all(a["artifact_id"] != m.artifact_id for a in await store.list(other))


@pytest.mark.parametrize("bad", ["", "art_x", "../acme/u1/art_" + "0" * 32, "art_" + "g" * 32, "ART_" + "0" * 32, None, 5])
async def test_malformed_ids_are_not_found(store, bad):
    with pytest.raises(ArtifactNotFound):
        await store.get(_ctx(), bad)


async def test_source_server_must_still_be_readable(store):
    ctx = _ctx(server="s2")
    m = await store.put(ctx, kind="compute", title=None, payload={"x": 1})
    # same user, but a key scoped only to s1 cannot read data that came from s2
    with pytest.raises(ArtifactNotFound):
        await store.get(_ctx(server="s1", scope=("s1",)), m.artifact_id)
    assert (await store.get(_ctx(server="s1", scope=("s1", "s2")), m.artifact_id))[1] == {"x": 1}
    # access to s2 revoked => gone, also from listings
    store._readable = Readable(ok=("s1",))
    with pytest.raises(ArtifactNotFound):
        await store.get(_ctx(server="s1"), m.artifact_id)
    assert all(a["artifact_id"] != m.artifact_id for a in await store.list(_ctx(server="s1")))


async def _raw_put(store, ctx, aid, body: bytes, **meta_over):
    meta = {"artifact_id": aid, "kind": "compute", "company_id": ctx.company_id, "user_id": ctx.user_id, "server_id": "s1",
            "server_type": "postgres", "key_id": "k", "title": "t", "created_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(), "sha256": __import__("hashlib").sha256(body).hexdigest(),
            "bytes": str(len(body))}
    meta.update(meta_over)
    async with store.storage.client() as c:
        await store.storage.ensure_bucket(c)
        await c.put_object(Bucket=BUCKET, Key=layout.artifact_key(ctx.company_id, ctx.user_id, aid), Body=body, Metadata=meta)


async def test_tampered_or_expired_objects_are_not_found(store):
    ctx = _ctx()
    body = orjson.dumps({"payload": {"x": 1}})
    cases = {
        "hash": {"sha256": "0" * 64},
        "company": {"company_id": "globex"},
        "user": {"user_id": "u2"},
        "expired": {"expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
        "id": {"artifact_id": "art_" + "f" * 32},
    }
    for over in cases.values():
        aid = f"art_{uuid.uuid4().hex}"
        await _raw_put(store, ctx, aid, body, **over)
        with pytest.raises(ArtifactNotFound):
            await store.get(ctx, aid)
    aid = f"art_{uuid.uuid4().hex}"
    await _raw_put(store, ctx, aid, body)
    assert (await store.get(ctx, aid))[1] == {"x": 1}  # control: untampered copy reads fine


async def test_size_limit(store):
    store.s = CommonSettings(s3_endpoint=ENDPOINT, s3_bucket=BUCKET, artifact_max_bytes=100)
    with pytest.raises(ArtifactRejected, match="limit"):
        await store.put(_ctx(), kind="table", title=None, payload={"rows": ["x" * 200]})


class PermReadable:
    """server -> granted permissions (stands in for the access records)."""

    def __init__(self, grants):
        self.grants = grants

    async def __call__(self, ctx, sid, permission="read"):
        return permission in self.grants.get(sid, set()) and (ctx.key_scope is None or sid in ctx.key_scope)


async def test_derived_artifact_keeps_every_source_server(store):
    # security review #2: a chart built on s1 from s2 data must keep re-authorizing s2
    store._readable = PermReadable({"s1": {"read", "export"}, "s2": {"read", "export"}})
    m = await store.put(_ctx(server="s1"), kind="chart", title="c", payload={"rows": [1]}, derived_from=("s2",))
    assert m.sources == ("s1", "s2")
    assert (await store.get(_ctx(server="s1"), m.artifact_id))[1] == {"rows": [1]}
    with pytest.raises(ArtifactNotFound):  # key scoped to s1 only
        await store.get(_ctx(server="s1", scope=("s1",)), m.artifact_id)
    store._readable = PermReadable({"s1": {"read", "export"}})  # s2 access revoked
    with pytest.raises(ArtifactNotFound):
        await store.get(_ctx(server="s1"), m.artifact_id)
    assert all(a["artifact_id"] != m.artifact_id for a in await store.list(_ctx(server="s1")))


async def test_export_permission_on_source_is_enforced_when_copying(store):
    # security review #3: data from a server where the user now has only `read` cannot become a file
    store._readable = PermReadable({"s1": {"read", "export"}, "s2": {"read"}})
    m = await store.put(_ctx(server="s2"), kind="compute", title=None, payload={"x": 1})
    assert (await store.get(_ctx(server="s1"), m.artifact_id))[1] == {"x": 1}  # viewing is fine
    with pytest.raises(ArtifactNotFound):
        await store.get(_ctx(server="s1"), m.artifact_id, permission="export")


async def test_provenance_metadata_must_match_body(store):
    ctx = _ctx()
    body = orjson.dumps({"payload": {"x": 1}, "sources": ["s1", "s2"]})
    aid = f"art_{uuid.uuid4().hex}"
    await _raw_put(store, ctx, aid, body, sources="s1")  # metadata narrowed to hide s2
    with pytest.raises(ArtifactNotFound):
        await store.get(ctx, aid)


async def test_listing_is_bounded_and_concurrent(store, monkeypatch):
    import mcpcluster_common.artifacts as am

    ctx = _ctx(user=f"lister-{uuid.uuid4().hex[:6]}")
    ids = {(await store.put(ctx, kind="table", title=f"t{i}", payload={"i": i})).artifact_id for i in range(12)}
    assert {a["artifact_id"] for a in await store.list(ctx, limit=200)} == ids
    monkeypatch.setattr(am, "LIST_SCAN_MAX", 5)
    assert len(await store.list(ctx, limit=200)) == 5  # never examines more than the cap


async def test_iac_managed_bucket_is_never_configured_by_the_app():
    from mcpcluster_common.storage import ObjectStorage

    class NoBucketCalls:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected bucket call {name}")  # head/create/lifecycle need permissions we don't grant

    st = ObjectStorage(CommonSettings(s3_manage_bucket=False))
    await st.ensure_bucket(NoBucketCalls())


async def test_objects_are_write_once(store):
    # mcp/ is immutable: a second write to an existing key is refused by the store, not silently replaced
    from botocore.exceptions import ClientError

    m = await store.put(_ctx(), kind="compute", title=None, payload={"x": 1})
    key = layout.artifact_key("acme", "u1", m.artifact_id)
    assert key.startswith("mcp/artifacts/tenant=acme/user=u1/")
    async with store.storage.client() as c:
        with pytest.raises(ClientError) as e:
            await store.storage.put_new(c, key, b"{}")
    assert e.value.response["Error"]["Code"] == "PreconditionFailed"
    assert (await store.get(_ctx(), m.artifact_id))[1] == {"x": 1}
