"""Interop with a different MCP implementation: the official MCP Inspector CLI (Node, TypeScript SDK) talks to the
gateway and a direct endpoint over Streamable HTTP with a Bearer header. Our own tests use the Python SDK only; this
catches protocol assumptions that only hold between two FastMCP peers. Needs docker + the compose stack."""

import json
import os
import shutil
import subprocess
import uuid

import httpx
import pytest

from mcp_gateway.server import assign_aliases

BACKEND = os.environ.get("E2E_BACKEND", "http://localhost:19000/api/v1")
HOST = "host.docker.internal"
# the Inspector runs in its own container and reaches the host-published nginx gateway via host-gateway
GW = os.environ.get("E2E_GATEWAY", "http://localhost:19000/mcp").replace("localhost", HOST, 1)
PG = os.environ.get("E2E_MCP_PG", "http://localhost:19000/mcp-postgres").replace("localhost", HOST, 1)
pytestmark = pytest.mark.e2e

try:
    _up = httpx.get(BACKEND.replace("/api/v1", "/healthz"), timeout=2).status_code == 200 and shutil.which("docker") is not None
except Exception:  # noqa: BLE001
    _up = False
if not _up:
    pytest.skip("compose stack or docker not available", allow_module_level=True)


def inspector(url: str, key: str, *args: str) -> tuple[int, str]:
    cmd = ["docker", "run", "--rm", "--add-host", f"{HOST}:host-gateway", "-v", "mcp-inspector-npm:/root/.npm", "node:22-alpine",
           "npx", "-y", "@modelcontextprotocol/inspector", "--cli", url, "--transport", "http",
           "--header", f"Authorization: Bearer {key}", *args]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    return p.returncode, p.stdout + p.stderr


def _json(out: str) -> dict:
    start = out.index("{")
    return json.loads(out[start: out.rindex("}") + 1])


@pytest.fixture(scope="module")
def world():
    with httpx.Client(base_url=BACKEND, timeout=20) as c:
        c.headers["Authorization"] = "Bearer " + c.post("/auth/login", json={"email": "master@example.com", "password": "master-dev-password-123"}).json()["access_token"]
        tag = uuid.uuid4().hex[:6]
        srv = c.post("/servers", json={"server_type": "postgres", "name": f"Compat PG {tag}"}).json()
        cid = f"compat-{tag}"
        c.post("/companies", json={"company_id": cid, "name": "Compat"})
        c.post(f"/companies/{cid}/bindings", json={"server_id": srv["server_id"], "tenant": {"mode": "schema", "schema_name": "acme"}})
        u = c.post(f"/companies/{cid}/users", json={"email": f"c-{cid}@ex.com", "name": "C", "role": "member"}).json()
        c.post(f"/companies/{cid}/users/{u['user_id']}/access", json={"server_id": srv["server_id"], "permissions": ["read"], "allowed_tools": ["run_select", "chart"]})
        raw = c.post(f"/companies/{cid}/api-keys", json={"user_id": u["user_id"], "server_ids": None, "name": "compat"}).json()["raw_key"]
    return {"key": raw, "sid": srv["server_id"], "alias": assign_aliases([(srv["server_id"], "postgres", srv["name"])])[srv["server_id"]]}


def test_gateway_list_and_text_call(world):
    code, out = inspector(GW, world["key"], "--method", "tools/list")
    assert code == 0, out
    names = {t["name"] for t in _json(out)["tools"]}
    assert {f"{world['alias']}__run_select", f"{world['alias']}__chart"} <= names
    code, out = inspector(GW, world["key"], "--method", "tools/call", "--tool-name", f"{world['alias']}__run_select",
                          "--tool-arg", "sql=select name from customers order by id")
    assert code == 0, out
    res = _json(out)
    assert not res.get("isError") and "Acme Alice" in json.dumps(res, ensure_ascii=False)


def test_gateway_image_result(world):
    spec = json.dumps({"type": "bar", "x": {"field": "name"}, "y": {"field": "id"}})
    src = json.dumps({"sql": "select name, id from customers order by id"})
    code, out = inspector(GW, world["key"], "--method", "tools/call", "--tool-name", f"{world['alias']}__chart",
                          "--tool-arg", f"spec={spec}", f"source={src}")
    assert code == 0, out
    content = _json(out)["content"]
    img = [b for b in content if b.get("type") == "image"]
    assert img and img[0]["mimeType"] == "image/png" and len(img[0]["data"]) > 1000


def test_direct_endpoint_with_server_param_and_bad_key(world):
    code, out = inspector(f"{PG}?server={world['sid']}", world["key"], "--method", "tools/list")
    assert code == 0, out
    assert {t["name"] for t in _json(out)["tools"]} == {"run_select", "chart"}
    code, out = inspector(GW, "mcpk_invalid", "--method", "tools/list")
    assert code != 0 and "unauthorized" in out.lower()
