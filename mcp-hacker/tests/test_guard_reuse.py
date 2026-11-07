"""The shared guard behaves for hacker tenants exactly as for mcp-postgres."""

import pytest

from mcpcluster_common.model import TenantSpec
from mcpcluster_common.postgres import SqlRejected, admit
from mcpcluster_common.postgres.pool import PoolConfig, dsn_for
from mcpcluster_common.context import TenantContext
from mcpcluster_common.settings import CommonSettings

ACME = TenantSpec(mode="schema", schema_name="acme")


def test_select_pinned_to_tenant_schema():
    out = admit("select title, bounty_usd from reports r join hackers h on h.handle = r.hacker_handle", "select", ACME)
    assert out.sql.count('"acme"') == 2


@pytest.mark.parametrize("sql", ["select * from globex.reports", "delete from reports", "update reports set status='x'", "select pg_read_file('/etc/passwd')"])
def test_rejects(sql):
    with pytest.raises(SqlRejected):
        admit(sql, "select", ACME)


def test_dsn_database_mode_and_credentials():
    from cryptography.fernet import Fernet

    from mcpcluster_common.secrets import fernet

    s = CommonSettings()
    enc = fernet(s).encrypt(b"pw").decode()
    ctx = TenantContext(company_id="c", user_id="u", key_id="k", server_id="s", server_type="hacker", permissions=frozenset({"read"}), allowed_tools=frozenset({"run_select"}),
                        tenant=TenantSpec(mode="database", database="acme_db", credentials={"username": "acme_ro", "password_enc": enc}), binding_version=1)
    dsn = dsn_for(PoolConfig(base_dsn="postgresql://mcp:secret@pg:5432/hacker", app_name="t"), s, ctx)
    assert dsn == "postgresql://acme_ro:pw@pg:5432/acme_db"
    assert isinstance(fernet(s), Fernet)


def test_dsn_credentials_are_percent_encoded():
    """A per-company password with URL metacharacters cannot redirect the connection or change the database."""
    from urllib.parse import unquote, urlsplit

    from mcpcluster_common.context import TenantContext
    from mcpcluster_common.model import TenantSpec
    from mcpcluster_common.postgres.pool import PoolConfig, dsn_for
    from mcpcluster_common.secrets import fernet
    from mcpcluster_common.settings import CommonSettings

    s = CommonSettings()
    evil = "p@evil.example:5432/other#?x"
    enc = fernet(s).encrypt(evil.encode()).decode()
    ctx = TenantContext(company_id="c", user_id="u", key_id="k", server_id="s", server_type="postgres", permissions=frozenset({"read"}),
                        allowed_tools=frozenset({"run_select"}), binding_version=1,
                        tenant=TenantSpec(mode="database", database="Weird DB/x", credentials={"username": "ac me@", "password_enc": enc}))
    u = urlsplit(dsn_for(PoolConfig(base_dsn="postgresql://mcp:pw@pg-sample:5432/sample", app_name="t"), s, ctx))
    assert u.hostname == "pg-sample" and u.port == 5432
    assert unquote(u.password) == evil and unquote(u.username) == "ac me@" and unquote(u.path[1:]) == "Weird DB/x"
