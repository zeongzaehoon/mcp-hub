import pytest

from mcp_postgres.sqlguard import SqlRejected, admit
from mcpcluster_common.model import TenantSpec

ACME = TenantSpec(mode="schema", schema_name="acme")
DB = TenantSpec(mode="database", database="acme_db")


def test_pins_unqualified_tables_to_tenant_schema():
    out = admit("select * from customers c join orders o on o.cid = c.id", "select", ACME)
    assert '"acme".customers' in out.sql.replace('"customers"', "customers") or '"acme"."customers"' in out.sql
    assert "orders" in out.sql and out.kind == "select"


@pytest.mark.parametrize("sql", [
    "select * from globex.customers",
    "select * from pg_catalog.pg_tables",
    "select * from information_schema.tables",
    "select * from acme.customers; drop table acme.customers",
    "insert into customers(name) values ('x')",
    "with d as (delete from customers returning *) select * from d",
    "select pg_read_file('/etc/passwd')",
    "select set_config('search_path','globex',false)",
    "set search_path to globex",
    "select * from mydb.globex.customers",
])
def test_rejects(sql):
    with pytest.raises(SqlRejected):
        admit(sql, "select", ACME)


def test_write_tools_require_matching_kind():
    assert admit("update customers set name='a' where id=1", "update", ACME).kind == "update"
    with pytest.raises(SqlRejected):
        admit("update customers set name='a'", "delete", ACME)
    with pytest.raises(SqlRejected):
        admit("delete from globex.customers", "delete", ACME)


def test_database_mode_allows_own_schemas_but_not_system():
    assert admit("select * from sales.orders", "select", DB).kind == "select"
    with pytest.raises(SqlRejected):
        admit("select * from pg_catalog.pg_class", "select", DB)


def test_column_mode_rejected():
    with pytest.raises(SqlRejected):
        admit("select 1", "select", TenantSpec(mode="column", column="company_id", value="acme"))


def test_semicolon_inside_literal_and_pg_family_block():
    assert admit("select 'a;b' as s from customers", "select", ACME).kind == "select"
    for sql in ("select pg_typeof(1) from customers", "select lo_get(1)", "select * from dblink_get_connections()"):
        with pytest.raises(SqlRejected):
            admit(sql, "select", ACME)


def test_quoted_mixed_case_schema_is_pinned_verbatim():
    t = TenantSpec(mode="schema", schema_name="Weird-Tenant")
    out = admit('select * from "Mixed Case"', "select", t).sql
    assert '"Weird-Tenant"."Mixed Case"' in out
