"""docs/CLIENTS.md is executable: every JSON block with a "step" is run, in order, through the gateway against the
compose stack. `{pg}` is the caller's postgres server alias, `{artifact:<step>}` the artifact saved by that step."""

import base64
import io
import json
import os
import re
import uuid
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from mcp_gateway.server import assign_aliases

BACKEND = os.environ.get("E2E_BACKEND", "http://localhost:19000/api/v1")
GW = os.environ.get("E2E_GATEWAY", "http://localhost:19000/mcp")
DOC = Path(__file__).resolve().parents[2] / "docs/CLIENTS.md"
pytestmark = pytest.mark.e2e

try:
    _up = httpx.get(BACKEND.replace("/api/v1", "/healthz"), timeout=2).status_code == 200
except Exception:  # noqa: BLE001
    _up = False
if not _up:
    pytest.skip("compose stack not running", allow_module_level=True)

TOOLS = ["list_tables", "run_select", "compute", "chart", "report", "get_artifact", "monitor_create", "monitor_delete"]


def steps() -> list[dict]:
    blocks = re.findall(r"```json\n(.*?)\n```", DOC.read_text(), re.DOTALL)
    return [json.loads(b) for b in blocks if '"step"' in b]


def test_guide_has_the_whole_flow():
    assert [s["step"] for s in steps()] == ["tables", "rows", "stats", "chart", "report", "monitor"]


async def test_every_documented_call_works_through_the_gateway():
    with httpx.Client(base_url=BACKEND, timeout=20) as c:
        tok = c.post("/auth/login", json={"email": "master@example.com", "password": "master-dev-password-123"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        tag = uuid.uuid4().hex[:6]
        server = c.post("/servers", json={"server_type": "postgres", "name": f"Guide PG {tag}"}).json()
        cid = f"guide-{tag}"
        assert c.post("/companies", json={"company_id": cid, "name": "Guide"}).status_code == 201
        assert c.post(f"/companies/{cid}/bindings", json={"server_id": server["server_id"], "tenant": {"mode": "schema", "schema_name": "acme"}}).status_code == 201
        u = c.post(f"/companies/{cid}/users", json={"email": f"g-{cid}@ex.com", "name": "G", "role": "member"}).json()
        r = c.post(f"/companies/{cid}/users/{u['user_id']}/access", json={"server_id": server["server_id"], "permissions": ["read", "export"], "allowed_tools": TOOLS})
        assert r.status_code == 201, r.text
        raw = c.post(f"/companies/{cid}/api-keys", json={"user_id": u["user_id"], "server_ids": None, "name": "guide"}).json()["raw_key"]
    alias = assign_aliases([(server["server_id"], "postgres", server["name"])])[server["server_id"]]
    saved: dict[str, str] = {}

    def fill(obj):
        if isinstance(obj, str):
            obj = obj.replace("{pg}", alias)
            return re.sub(r"\{artifact:(\w+)\}", lambda m: saved[m.group(1)], obj)
        if isinstance(obj, dict):
            return {k: fill(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [fill(v) for v in obj]
        return obj

    results = {}
    async with Client(StreamableHttpTransport(GW, headers={"Authorization": f"Bearer {raw}"})) as cl:
        for s in steps():
            res = await cl.call_tool(fill(s["tool"]), fill(s["arguments"]))
            out = res.structured_content or {}
            results[s["step"]] = (res, out)
            if isinstance(out.get("artifact"), dict):
                saved[s["step"]] = out["artifact"]["artifact_id"]
        assert [r["name"] for r in results["rows"][1]["rows"]] == ["Acme Alice", "Acme Bob"]
        assert {r["email"] for r in results["rows"][1]["rows"]} == {"[EMAIL]"}
        assert any(b.type == "image" for b in results["chart"][0].content)
        from docx import Document  # noqa: PLC0415 — dev dependency of the gateway tests

        doc = Document(io.BytesIO(base64.b64decode(results["report"][1]["content_base64"])))
        assert str(results["stats"][1]["result"]["count"]) in [c.text for t in doc.tables for row in t.rows for c in row.cells]
        await cl.call_tool(f"{alias}__monitor_delete", {"monitor_id": results["monitor"][1]["monitor_id"]})
