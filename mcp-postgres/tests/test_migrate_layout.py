"""Legacy monitor objects move into the new layout once, verified, never re-keyed from untrusted paths."""

import os
import uuid

import httpx
import orjson
import pytest

from mcpcluster_common import migrate_layout as mig
from mcpcluster_common.settings import CommonSettings
from mcpcluster_common.storage import ObjectStorage

ENDPOINT = os.environ.get("TEST_S3_ENDPOINT", "http://localhost:9000")
DEF = {"monitor_id": "mon_1", "company_id": "co_a", "user_id": "usr_1", "server_type": "postgres", "server_id": "s1"}


def test_plan_key_requires_path_and_document_to_agree():
    assert mig.plan_key("monitors/postgres/co_a/usr_1/mon_1.json", DEF) == "control/monitors/server_type=postgres/tenant=co_a/user=usr_1/mon_1.json"
    assert mig.plan_key("monitor-state/co_a/usr_1/mon_1.json", {"events": []}) == "control/monitor_state/tenant=co_a/user=usr_1/mon_1.json"
    # an object placed under another company's path is not trusted into that company's partition
    assert mig.plan_key("monitors/postgres/co_b/usr_1/mon_1.json", DEF) is None
    assert mig.plan_key("monitors/postgres/co_a/usr_2/mon_1.json", DEF) is None
    assert mig.plan_key("monitor-state/a=b/usr_1/mon_1.json", {}) is None
    assert mig.plan_key("artifacts/co_a/usr_1/art_1.json", DEF) is None


def _up() -> bool:
    try:
        return httpx.get(f"{ENDPOINT}/minio/health/live", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _up(), reason="object storage not reachable")
async def test_migrate_dry_run_then_execute_is_idempotent(capsys):
    src, dst = f"test-legacy-{uuid.uuid4().hex[:8]}", f"test-new-{uuid.uuid4().hex[:8]}"
    s = CommonSettings(s3_endpoint=ENDPOINT, s3_bucket=dst)
    st = ObjectStorage(s)
    async with st.client() as c:
        await c.create_bucket(Bucket=src, CreateBucketConfiguration={"LocationConstraint": s.s3_region})
        await c.put_object(Bucket=src, Key="monitors/postgres/co_a/usr_1/mon_1.json", Body=orjson.dumps(DEF))
        await c.put_object(Bucket=src, Key="monitor-state/co_a/usr_1/mon_1.json", Body=b'{"events":[]}')
        await c.put_object(Bucket=src, Key="monitors/postgres/co_b/usr_1/mon_1.json", Body=orjson.dumps(DEF))  # mismatched
        await c.put_object(Bucket=src, Key="artifacts/co_a/usr_1/art_1.json", Body=b"{}")  # TTL output: left alone
    try:
        assert await mig.migrate(s, src, execute=False, delete_legacy=False) == {"moved": 0, "already": 0, "conflict": 0, "skipped": 1, "deleted": 0, "planned": 2}
        r = await mig.migrate(s, src, execute=True, delete_legacy=False)
        assert (r["moved"], r["skipped"]) == (2, 1)
        r = await mig.migrate(s, src, execute=True, delete_legacy=True)  # re-run: same bytes => only cleans the source
        assert (r["moved"], r["already"], r["deleted"]) == (0, 2, 2)
        async with st.client() as c:
            new = await c.get_object(Bucket=dst, Key="control/monitors/server_type=postgres/tenant=co_a/user=usr_1/mon_1.json")
            async with new["Body"] as b:
                assert orjson.loads(await b.read()) == DEF
            left = sorted(o["Key"] for o in (await c.list_objects_v2(Bucket=src)).get("Contents", []))
            assert left == ["artifacts/co_a/usr_1/art_1.json", "monitors/postgres/co_b/usr_1/mon_1.json"]
    finally:
        async with st.client() as c:
            for b in (src, dst):
                for o in (await c.list_objects_v2(Bucket=b)).get("Contents", []):
                    await c.delete_object(Bucket=b, Key=o["Key"])
                await c.delete_bucket(Bucket=b)
