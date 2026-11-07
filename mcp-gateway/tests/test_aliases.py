"""Namespace aliases and upstream parsing (pure)."""

from mcp_gateway.server import MAX_ALIAS, assign_aliases, parse_upstreams, slug


def test_slug():
    assert slug("PG Sample (prod)") == "pg-sample-prod"
    assert slug("한글 서버") == "server" and slug("") == "server"
    assert len(slug("x" * 100)) == MAX_ALIAS


def test_aliases_unique_stable_and_tool_name_safe():
    servers = [("srv_b0000000000000000001", "postgres", "Sample"), ("srv_a0000000000000000002", "postgres", "sample"),
               ("srv_c0000000000000000003", "clickhouse", "")]
    a = assign_aliases(servers)
    assert a == assign_aliases(list(reversed(servers)))  # order-independent
    assert len(set(a.values())) == 3 and a["srv_a0000000000000000002"] == "sample-0002" and a["srv_b0000000000000000001"] == "sample-0001"
    assert a["srv_c0000000000000000003"] == "clickhouse-0003"
    # security review #7: adding a same-named server never changes an existing alias
    more = assign_aliases([*servers, ("srv_00000000000000000000", "postgres", "sample")])
    assert all(more[k] == v for k, v in a.items())
    for alias in a.values():
        assert len(f"{alias}__describe_table") <= 64 and all(ch.isalnum() or ch == "-" for ch in alias)


def test_parse_upstreams():
    assert parse_upstreams("postgres=http://a:1/mcp, mongo=http://b:2/mcp,,bad") == {"postgres": "http://a:1/mcp", "mongo": "http://b:2/mcp"}


def test_gateway_instructions_carry_the_compute_rule():
    """The client only ever sees the gateway's instructions (upstream ones are not forwarded), so the rule that
    figures come from `compute`, not from the model, must live here."""
    from mcp_gateway.server import INSTRUCTIONS

    assert "compute" in INSTRUCTIONS and "never calculate" in INSTRUCTIONS
    assert "Tools are named <server>__<tool>" in INSTRUCTIONS
