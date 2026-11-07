"""Pure domain invariants — no I/O."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import InvariantViolation
from app.domain.enums import Permission, Role, ServerType, Status
from app.domain.models import ApiKey, ServerAccess, TenantBinding, User
from app.infra.dynamo.keys import from_item, to_item
from app.services.companies import assert_transition


def _key(**over):
    base = {"company_id": "acme", "user_id": "u", "server_ids": ["s"], "key_hash": "a" * 64, "key_prefix": "mcpk_x", "name": "k"}
    base.update(over)
    return ApiKey(**base)


def _access(**over):
    base = {"company_id": "acme", "user_id": "u", "server_id": "s", "server_type": ServerType.POSTGRES,
            "permissions": {Permission.READ}, "allowed_tools": {"run_select"}}
    base.update(over)
    return ServerAccess(**base)


def test_access_rejects_unknown_tool():
    with pytest.raises(ValueError, match="unknown tools"):
        _access(allowed_tools={"drop_everything"})


def test_access_rejects_tool_not_covered_by_permissions():
    with pytest.raises(ValueError, match="require permissions"):
        _access(allowed_tools={"run_delete"}, permissions={Permission.READ})


def test_access_accepts_covered_tools():
    a = _access(allowed_tools={"run_select", "run_delete"}, permissions={Permission.READ, Permission.DELETE})
    assert a.allowed_tools == {"run_select", "run_delete"}


def test_access_rejects_empty_sets():
    with pytest.raises(ValueError):
        _access(allowed_tools=set())
    with pytest.raises(ValueError):
        _access(permissions=set())


def test_key_scope_rules():
    assert _key(server_ids=["b", "a"]).server_ids == ["a", "b"]
    assert _key(server_ids=None).server_ids is None  # all of the user's servers
    with pytest.raises(ValueError, match="must not be empty"):
        _key(server_ids=[])
    with pytest.raises(ValueError, match="unique"):
        _key(server_ids=["a", "a"])
    with pytest.raises(ValueError, match="at most"):
        _key(server_ids=[f"s{i}" for i in range(51)])
    with pytest.raises(ValueError):  # scope is required: omission must never mean "all"
        ApiKey(company_id="acme", user_id="u", key_hash="a" * 64, key_prefix="mcpk_x", name="k")


def test_key_scope_roundtrips_through_dynamo_item():
    for scope in (None, ["a", "b"]):
        k = _key(server_ids=scope)
        item = to_item(k)
        item = {kk: v for kk, v in item.items() if v is not None}  # None attributes are not stored
        assert from_item(item).server_ids == scope


def test_legacy_key_item_reads_as_single_server_scope():
    legacy = {"entity_type": "APIKEY", "PK": "COMPANY#acme", "SK": "APIKEY#k", "key_id": "k", "company_id": "acme",
              "user_id": "u", "server_id": "srv_1", "server_type": "postgres", "key_hash": "a" * 64,
              "key_prefix": "mcpk_x", "name": "k", "permissions": ["read"], "allowed_tools": ["run_select"],
              "status": "active", "version": 1, "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00"}
    assert from_item(legacy).server_ids == ["srv_1"]


def test_key_rejects_naive_expiry():
    with pytest.raises(ValueError, match="timezone"):
        _key(expires_at=datetime(2030, 1, 1))  # noqa: DTZ001 — naive on purpose


def test_key_expiry():
    assert _key(expires_at=datetime.now(UTC) - timedelta(seconds=1)).is_expired()
    assert not _key(expires_at=datetime.now(UTC) + timedelta(days=1)).is_expired()


def test_binding_mode_per_server_type():
    TenantBinding(company_id="acme", server_id="s", server_type=ServerType.POSTGRES, tenant={"mode": "schema", "schema_name": "acme"})
    with pytest.raises(ValueError, match="not supported"):
        TenantBinding(company_id="acme", server_id="s", server_type=ServerType.POSTGRES, tenant={"mode": "column", "column": "company_id", "value": "acme"})
    with pytest.raises(ValueError, match="not supported"):
        TenantBinding(company_id="acme", server_id="s", server_type=ServerType.MONGO, tenant={"mode": "schema", "schema_name": "x"})
    with pytest.raises(ValueError):  # quote / semicolon can never be carried safely
        TenantBinding(company_id="acme", server_id="s", server_type=ServerType.POSTGRES, tenant={"mode": "schema", "schema_name": 'acme"; drop schema globex; --'})
    # anything quotable is allowed: engines are not assumed to use lower_snake identifiers
    TenantBinding(company_id="acme", server_id="s", server_type=ServerType.POSTGRES, tenant={"mode": "schema", "schema_name": "Weird-Tenant"})
    TenantBinding(company_id="acme", server_id="s", server_type=ServerType.CLICKHOUSE, tenant={"mode": "column", "column": "company_id", "value": "acme", "database": "weird_db"})
    with pytest.raises(ValueError, match="MongoDB"):
        TenantBinding(company_id="acme", server_id="s", server_type=ServerType.MONGO, tenant={"mode": "database", "database": "has.dot"})


def test_master_only_in_system_company():
    with pytest.raises(ValueError):
        User(company_id="acme", email="a@b.c", name="x", role=Role.MASTER)
    with pytest.raises(ValueError):
        User(company_id="system", email="a@b.c", name="x", role=Role.ADMIN)


def test_status_transitions():
    assert_transition(Status.ACTIVE, Status.SUSPENDED)
    assert_transition(Status.SUSPENDED, Status.ACTIVE)
    with pytest.raises(InvariantViolation):
        assert_transition(Status.DELETED, Status.ACTIVE)


def test_export_is_a_separate_permission():
    with pytest.raises(ValueError, match="require permissions"):
        _access(allowed_tools={"run_select", "export"}, permissions={Permission.READ})
    a = _access(allowed_tools={"run_select", "export", "transform"}, permissions={Permission.READ, Permission.EXPORT})
    assert "export" in a.allowed_tools
