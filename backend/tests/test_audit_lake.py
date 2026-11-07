"""Access-log lake on S3 (app/infra/s3/audit.py): Hive partitions per tenant and UTC day, append-only, replay-safe."""

import io

import httpx
import pyarrow.parquet as pq
import pytest

from app.core.config import get_settings
from app.infra.s3 import audit


def _ev(cid="co_a", ts="2026-10-01T23:30:00+09:00", **kw):
    return {"ts": ts, "key_id": "key_1", "company_id": cid, "user_id": "usr_1", "server_id": "srv_1", "server_type": "postgres",
            "tool": "query", "args_summary": "select 1", "status": "ok", "duration_ms": 3, "extra": {"rows": 1}, **kw}


def test_partitions_by_tenant_and_utc_day():
    files = audit.partition([("1-0", _ev()), ("2-0", _ev(cid="co_b")), ("3-0", _ev(ts="2026-10-02T01:00:00+00:00")), ("4-0", _ev())])
    assert set(files) == {
        "audit/access_logs/tenant=co_a/dt=20261001/1-0_4-0.parquet",
        "audit/access_logs/tenant=co_b/dt=20261001/2-0_2-0.parquet",
        "audit/access_logs/tenant=co_a/dt=20261002/3-0_3-0.parquet",
    }
    t = pq.read_table(io.BytesIO(files["audit/access_logs/tenant=co_a/dt=20261001/1-0_4-0.parquet"]))
    assert t.column("stream_id").to_pylist() == ["1-0", "4-0"]
    assert "company_id" not in t.column_names and "dt" not in t.column_names  # partition values live in the path
    assert t.column("extra_json").to_pylist()[0] == '{"rows":1}'


@pytest.mark.parametrize("cid", ["", "unknown", "../co_b", "co_a/x", "a=b", None, 7])
def test_missing_or_path_changing_tenant_goes_to_default_partition(cid):
    (key,) = audit.partition([("1-0", _ev(cid=cid, ts="garbage"))])
    assert key == "audit/access_logs/tenant=_/dt=_/1-0_1-0.parquet"


def _s3_up() -> bool:
    try:
        return httpx.get(f"{get_settings().s3_endpoint}/minio/health/live", timeout=2).status_code == 200
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _s3_up(), reason="object storage not reachable")
async def test_write_is_append_only_and_replay_safe():
    lake = audit.AuditLake(get_settings())
    events = [("10-0", _ev()), ("11-0", _ev(cid="co_b"))]
    assert await lake.write(events) == 2
    assert await lake.write(events) == 2  # replay: same keys, 412 swallowed, nothing overwritten
    async with lake._client() as c:
        listed = await c.list_objects_v2(Bucket=lake.s.s3_bucket, Prefix="audit/access_logs/")
        keys = sorted(o["Key"] for o in listed.get("Contents", []))
        assert "audit/access_logs/tenant=co_a/dt=20261001/10-0_10-0.parquet" in keys
        assert "audit/access_logs/tenant=co_b/dt=20261001/11-0_11-0.parquet" in keys
        obj = await c.get_object(Bucket=lake.s.s3_bucket, Key="audit/access_logs/tenant=co_b/dt=20261001/11-0_11-0.parquet")
        async with obj["Body"] as b:
            t = pq.read_table(io.BytesIO(await b.read()))
        assert t.num_rows == 1 and t.column("user_id").to_pylist() == ["usr_1"]
        for o in listed.get("Contents", []):
            await c.delete_object(Bucket=lake.s.s3_bucket, Key=o["Key"])
