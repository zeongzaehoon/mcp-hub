import ast
import pathlib

import pytest

from mcp_hacker.server import TOOL_PERMISSIONS, install_auth, mcp, registry


def _backend_catalog() -> dict[str, str]:
    p = pathlib.Path(__file__).resolve().parents[2] / "backend/app/domain/tool_catalog.py"
    tree = ast.parse(p.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "TOOL_CATALOG":
            for k, v in zip(node.value.keys, node.value.values, strict=True):
                if isinstance(k, ast.Attribute) and k.attr == "HACKER":
                    return {kk.value: vv.attr.lower() for kk, vv in zip(v.keys, v.values, strict=True)}
    raise AssertionError("catalog not found")


def test_permissions_match_backend():
    assert TOOL_PERMISSIONS == _backend_catalog()


def test_catalog_has_no_write_permissions():
    assert set(TOOL_PERMISSIONS.values()) <= {"read", "export"}


def test_pool_is_read_only():
    assert registry is not None
    from mcp_hacker import server

    assert server.registry._factory.__closure__ is not None  # built by make_registry(read_only=True)


async def test_registered_tools_match_permissions():
    assert {t.name for t in await mcp.list_tools()} == set(TOOL_PERMISSIONS)


async def test_auth_middleware_denies_without_key():
    from mcp.shared.exceptions import MCPError

    install_auth(mcp)
    try:
        with pytest.raises(MCPError, match="unauthorized"):
            await mcp.list_tools()
    finally:
        mcp.middleware.clear()
