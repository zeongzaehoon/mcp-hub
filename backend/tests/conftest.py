import os
import uuid

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("DYNAMO_ENDPOINT", "http://localhost:8000")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("RECONCILE_INTERVAL_SECONDS", "3600")
os.environ.setdefault("OUTBOX_POLL_SECONDS", "3600")
os.environ.setdefault("TABLE_NAME", f"test_mcp_cluster_{uuid.uuid4().hex[:6]}")
os.environ.setdefault("LOG_TABLE_NAME", f"test_mcp_cluster_logs_{uuid.uuid4().hex[:6]}")
os.environ.setdefault("REDIS_DB_AUTH", "11")
os.environ.setdefault("REDIS_DB_HOT", "12")
os.environ.setdefault("REDIS_DB_LOGS", "13")
os.environ.setdefault("SSO_HANDOFF_SECRET", "test-handoff-secret-0123456789abcdef")
# platform Google client against the in-process mock provider (tests/test_sso.py MockIdp serves /google and /directory)
os.environ.setdefault("GOOGLE_CLIENT_ID", "platform-google")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "platform-google-secret")
os.environ.setdefault("GOOGLE_ISSUER", "http://idp.test/google")
os.environ.setdefault("GOOGLE_DIRECTORY_URL", "http://idp.test/directory")
os.environ.setdefault("GOOGLE_TOKEN_URL", "http://idp.test/google/token")
os.environ.setdefault("WORKSPACE_SYNC_ON_CONNECT", "0")  # tests trigger syncs explicitly
os.environ.setdefault("WORKSPACE_SYNC_INTERVAL_S", "3600")
# audit lake: aws-local MinIO (dummy root creds), a throwaway bucket per run — never the default AWS chain
os.environ.setdefault("S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("S3_BUCKET", f"test-mcp-audit-{uuid.uuid4().hex[:6]}")
os.environ.setdefault("S3_ACCESS_KEY", "minioadmin")
os.environ.setdefault("S3_SECRET_KEY", "minioadmin123")

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import app
from tests.mock_idp import MockIdp


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
async def running_app():
    async with app.router.lifespan_context(app):
        c = app.state.container
        await c.pools.auth.flushdb()
        await c.pools.logs.flushdb()
        yield app
    s = get_settings()
    async with app.state.container.repo.factory.client() as cl:
        for t in (s.table_name, s.log_table_name):
            try:
                await cl.delete_table(TableName=t)
            except Exception:  # noqa: BLE001, S110
                pass
    await _drop_audit_bucket(s)


async def _drop_audit_bucket(s) -> None:
    from app.infra.s3.audit import AuditLake

    async with AuditLake(s)._client() as c:
        try:
            async for page in c.get_paginator("list_objects_v2").paginate(Bucket=s.s3_bucket):
                for o in page.get("Contents", []):
                    await c.delete_object(Bucket=s.s3_bucket, Key=o["Key"])
            await c.delete_bucket(Bucket=s.s3_bucket)
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup
            pass


@pytest.fixture
async def client(running_app):
    async with AsyncClient(transport=ASGITransport(app=running_app), base_url="http://test") as c:
        yield c


@pytest.fixture
def container(running_app):
    return running_app.state.container


@pytest.fixture
async def master(client):
    s = get_settings()
    r = await client.post("/api/v1/auth/login", json={"email": s.master_email, "password": s.master_password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def uid(prefix: str = "c") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def world(client, master):
    """company + admin + member + postgres server + active binding."""
    cid = uid("acme-")
    r = await client.post("/api/v1/companies", json={"company_id": cid, "name": "Acme"}, headers=master)
    assert r.status_code == 201, r.text
    r = await client.post(f"/api/v1/companies/{cid}/users", json={"email": f"admin-{cid}@ex.com", "name": "Admin", "role": "admin", "password": "adminpassword1"}, headers=master)
    assert r.status_code == 201, r.text
    admin = r.json()
    r = await client.post(f"/api/v1/companies/{cid}/users", json={"email": f"member-{cid}@ex.com", "name": "Member", "role": "member"}, headers=master)
    assert r.status_code == 201, r.text
    member = r.json()
    r = await client.post("/api/v1/servers", json={"server_type": "postgres", "name": f"pg-{cid}", "endpoint": "http://mcp-postgres:9001/mcp"}, headers=master)
    assert r.status_code == 201, r.text
    server = r.json()
    r = await client.post(f"/api/v1/companies/{cid}/bindings", json={"server_id": server["server_id"], "tenant": {"mode": "schema", "schema_name": "acme"}}, headers=master)
    assert r.status_code == 201, r.text
    r = await grant(client, master, cid, member["user_id"], server["server_id"])
    assert r.status_code == 201, r.text
    r = await client.post("/api/v1/auth/login", json={"email": admin["email"], "password": "adminpassword1"})
    admin_hdr = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return {"cid": cid, "admin": admin, "member": member, "server": server, "admin_hdr": admin_hdr}


async def grant(client, hdr, cid, user_id, server_id, permissions=("read",), tools=("list_tables", "run_select")):
    return await client.post(f"/api/v1/companies/{cid}/users/{user_id}/access", json={"server_id": server_id, "permissions": list(permissions), "allowed_tools": list(tools)}, headers=hdr)


async def issue_key(client, hdr, w, **over):
    body = {"user_id": w["member"]["user_id"], "server_ids": [w["server"]["server_id"]], "name": "k1"}
    body.update(over)
    return await client.post(f"/api/v1/companies/{w['cid']}/api-keys", json=body, headers=hdr)


@pytest.fixture
def idp(container):
    """In-process OIDC + Google Directory stand-in, wired into the app's relying-party client."""
    m = MockIdp()
    container.oidc.http = httpx.AsyncClient(transport=m.transport)
    container.oidc._disc.clear()
    container.oidc._jwks.clear()
    return m
