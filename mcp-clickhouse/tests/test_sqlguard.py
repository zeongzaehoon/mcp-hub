import pytest

from mcp_clickhouse.sqlguard import SqlRejected, admit
from mcpcluster_common.model import TenantSpec

COL = TenantSpec(mode="column", column="company_id", value="acme")
DB = TenantSpec(mode="database", database="acme_ch")
DEFAULT = "analytics"
# sqlglot renders quoted ClickHouse identifiers with double quotes (valid ClickHouse syntax)
FILTER = '''"company_id" = 'acme\''''


def test_column_mode_wraps_every_table():
    out = admit("SELECT e.kind, count() FROM events e JOIN pageviews p ON p.ts = e.ts GROUP BY e.kind", COL, DEFAULT).sql
    assert out.count(FILTER) == 2
    assert f'''FROM "analytics"."events" WHERE {FILTER}) AS "e"''' in out
    assert f'''FROM "analytics"."pageviews" WHERE {FILTER}) AS "p"''' in out


def test_column_mode_filters_subqueries_and_ctes_but_not_cte_refs():
    sql = "WITH big AS (SELECT * FROM events WHERE value > 10) SELECT * FROM big WHERE ts IN (SELECT ts FROM pageviews)"
    out = admit(sql, COL, DEFAULT).sql
    assert out.count(FILTER) == 2  # events + pageviews; `big` untouched
    assert "FROM big" in out


def test_column_mode_alias_preserved_when_absent():
    out = admit("SELECT events.value FROM events", COL, DEFAULT).sql
    assert ') AS "events"' in out


def test_column_mode_literal_value_is_escaped():
    t = TenantSpec(mode="column", column="company_id", value="ac'me")
    out = admit("SELECT 1 FROM events", t, DEFAULT).sql
    assert '''"company_id" = 'ac''me\'''' in out or '''"company_id" = 'ac\\'me\'''' in out


def test_database_mode_pins_tenant_db():
    out = admit("SELECT * FROM orders", DB, DEFAULT).sql
    assert 'FROM "acme_ch".orders' in out
    with pytest.raises(SqlRejected):
        admit("SELECT * FROM globex_ch.orders", DB, DEFAULT)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM system.tables",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM other_db.events",
    "SELECT * FROM url('http://x/', CSV, 'a String')",
    "SELECT * FROM remote('1.2.3.4', analytics.events)",
    "SELECT * FROM s3('http://x/y.csv')",
    "SELECT * FROM numbers(10) SETTINGS readonly=0",
    "SELECT * FROM events FORMAT JSON",
    "SELECT dictGet('d', 'v', 1) FROM events",
    "SELECT * FROM events; DROP TABLE events",
    "INSERT INTO events VALUES ('x', now(), 'k', 1)",
    "ALTER TABLE events DELETE WHERE 1",
    "SELECT currentUser()",
])
def test_rejects(sql):
    with pytest.raises(SqlRejected):
        admit(sql, COL, DEFAULT)


def test_column_mode_can_pin_its_own_database():
    t = TenantSpec(mode="column", column="company_id", value="acme", database="weird_db")
    out = admit("SELECT 1 FROM `Mixed Case`", t, DEFAULT).sql
    assert f'''FROM "weird_db"."Mixed Case" WHERE {FILTER}''' in out
    with pytest.raises(SqlRejected):
        admit("SELECT 1 FROM analytics.events", t, DEFAULT)  # other db now outside the tenant


def test_semicolon_inside_literal_is_fine():
    assert admit("SELECT 'a;b' AS s FROM events", COL, DEFAULT).sql


def test_schema_mode_not_supported():
    with pytest.raises(SqlRejected):
        admit("SELECT 1", TenantSpec(mode="schema", schema_name="x"), DEFAULT)
