"""SQL admission control for tenant-scoped execution.

Guarantees (schema mode):
  * exactly one statement, of the type the tool promises (SELECT / INSERT / UPDATE / DELETE);
  * every table reference is unqualified or qualified with the tenant schema — never another
    schema, never pg_catalog / information_schema;
  * no data-modifying CTEs inside a SELECT, no nested writes inside a write;
  * no session/config manipulation or file/network functions.
Database mode relaxes the qualifier rule to "no system schemas" since the connection itself is
scoped to the tenant database. Column mode is rejected here (backend also rejects it for postgres).
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from mcpcluster_common.model import TenantSpec

SYSTEM_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"}
FORBIDDEN_FUNCS = {"set_config", "current_setting", "query_to_xml", "query_to_xmlschema", "query_to_xml_and_xmlschema", "copy", "xpath"}
# whole families: server admin/introspection (pg_*), large objects (lo_*), cross-db (dblink*)
FORBIDDEN_PREFIXES = ("pg_", "lo_", "dblink")
WRITE_TYPES = (exp.Insert, exp.Update, exp.Delete, exp.Merge)
DDL_OR_ADMIN = (exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Set, exp.Transaction, exp.Commit, exp.Rollback, exp.Grant)


@dataclass(frozen=True)
class Admitted:
    sql: str
    kind: str  # select|insert|update|delete


class SqlRejected(ValueError):
    pass


def _statement_kind(tree: exp.Expression) -> str:
    root = tree
    if isinstance(root, exp.Insert):
        return "insert"
    if isinstance(root, exp.Update):
        return "update"
    if isinstance(root, exp.Delete):
        return "delete"
    if isinstance(root, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        return "select"
    raise SqlRejected(f"statement type {type(root).__name__} is not allowed")


def admit(sql: str, expected_kind: str, tenant: TenantSpec) -> Admitted:
    if tenant.mode == "column":
        raise SqlRejected("column-mode tenants are not supported by mcp-postgres yet")
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError as e:
        raise SqlRejected(f"cannot parse SQL: {e}") from e
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlRejected("exactly one statement is required")
    tree = statements[0]
    kind = _statement_kind(tree)
    if kind != expected_kind:
        raise SqlRejected(f"this tool only accepts {expected_kind.upper()} statements (got {kind.upper()})")

    # nested writes (data-modifying CTEs, subqueries with writes) are never allowed
    writes = [n for n in tree.walk() if isinstance(n, WRITE_TYPES)]
    if kind == "select" and writes:
        raise SqlRejected("data-modifying expressions are not allowed in SELECT")
    if kind != "select" and len(writes) != 1:
        raise SqlRejected("only a single top-level write is allowed")
    if any(isinstance(n, DDL_OR_ADMIN) for n in tree.walk()):
        raise SqlRejected("DDL / session commands are not allowed")

    for fn in tree.find_all(exp.Anonymous, exp.Func):
        name = (fn.name or getattr(fn, "sql_name", lambda: "")()).lower()
        if name in FORBIDDEN_FUNCS or name.startswith(FORBIDDEN_PREFIXES):
            raise SqlRejected(f"function {name}() is not allowed")

    schema = tenant.schema_name if tenant.mode == "schema" else None
    for table in tree.find_all(exp.Table):
        db = table.args.get("db")
        catalog = table.args.get("catalog")
        if catalog is not None:
            raise SqlRejected("catalog-qualified references are not allowed")
        qual = db.name if db is not None else None
        if qual and qual.lower() in SYSTEM_SCHEMAS:
            raise SqlRejected(f"schema {qual} is not accessible")
        if schema is not None and qual is not None and qual != schema:
            raise SqlRejected(f"schema {qual} is outside this tenant")
        if schema is not None and qual is None:
            table.set("db", exp.to_identifier(schema, quoted=True))  # pin explicitly; don't rely on search_path alone
    return Admitted(sql=tree.sql(dialect="postgres"), kind=kind)
