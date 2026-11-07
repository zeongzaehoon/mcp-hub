"""SQL admission control for ClickHouse tenants.

Guarantees:
  * exactly one SELECT (UNION/… allowed); no SETTINGS/FORMAT/INTO OUTFILE clauses;
  * no table functions (url, s3, remote, mysql, …), no dictionary lookups, no system/information_schema;
  * database mode: every table is unqualified or qualified with the tenant database;
  * column mode: every *real* table reference (not a CTE) is replaced by
        (SELECT * FROM db.table WHERE <col> = '<value>') AS <alias>
    so joins, subqueries, IN (...) and CTE bodies are all filtered — there is no unfiltered path.
The client additionally runs with readonly=1, result row caps and an execution timeout.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from mcpcluster_common.model import TenantSpec

SYSTEM_DBS = {"system", "information_schema"}
FORBIDDEN_FUNCS = {
    "url", "file", "s3", "s3cluster", "gcs", "azureblobstorage", "hdfs", "hdfscluster", "remote", "remotesecure",
    "mysql", "postgresql", "sqlite", "mongodb", "redis", "jdbc", "odbc", "cluster", "clusterallreplicas",
    "executable", "input", "merge", "view", "format", "deltalake", "iceberg", "hudi", "dictionary",
    "generaterandom", "fuzzjson", "fuzzquery", "loop", "hostname", "currentuser", "getsetting", "getservermetadata",
    "filesystemavailable", "filesystemcapacity", "filesystemunreserved",
}
FORBIDDEN_PREFIXES = ("dictget", "dicthas", "dictgetordefault")


@dataclass(frozen=True)
class Admitted:
    sql: str


class SqlRejected(ValueError):
    pass


def _func_name(fn: exp.Expression) -> str:
    if isinstance(fn, exp.Anonymous):
        return fn.name.lower()
    return fn.sql_name().lower()


def _check_functions(tree: exp.Expression) -> None:
    for fn in tree.find_all(exp.Func):
        name = _func_name(fn)
        if name in FORBIDDEN_FUNCS or name.startswith(FORBIDDEN_PREFIXES):
            raise SqlRejected(f"function {name}() is not allowed")


def _check_clauses(tree: exp.Expression) -> None:
    for sel in tree.find_all(exp.Select):
        if sel.args.get("settings"):
            raise SqlRejected("SETTINGS clause is not allowed")
        if sel.args.get("format"):
            raise SqlRejected("FORMAT clause is not allowed")
        if sel.args.get("into"):
            raise SqlRejected("INTO OUTFILE is not allowed")
    if tree.args.get("settings") or tree.args.get("format"):
        raise SqlRejected("SETTINGS/FORMAT clauses are not allowed")


def _cte_names(tree: exp.Expression) -> set[str]:
    return {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}


def admit(sql: str, tenant: TenantSpec, default_db: str) -> Admitted:
    try:
        statements = [s for s in sqlglot.parse(sql, read="clickhouse") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SqlRejected(f"cannot parse SQL: {e}") from e
    if len(statements) != 1:
        raise SqlRejected("exactly one statement is required")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SqlRejected(f"only SELECT statements are allowed (got {type(tree).__name__})")
    if any(isinstance(n, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Set)) for n in tree.walk()):
        raise SqlRejected("only SELECT statements are allowed")
    _check_clauses(tree)
    _check_functions(tree)

    if tenant.mode == "database":
        allowed_db = tenant.database
    elif tenant.mode == "column":
        allowed_db = tenant.database or default_db
    else:
        raise SqlRejected(f"tenant mode {tenant.mode} is not supported by mcp-clickhouse")

    ctes = _cte_names(tree)
    for table in list(tree.find_all(exp.Table)):
        if not isinstance(table.this, exp.Identifier):
            raise SqlRejected("table functions are not allowed")
        if table.args.get("catalog") is not None:
            raise SqlRejected("catalog-qualified references are not allowed")
        db = table.args.get("db")
        qual = db.name if db is not None else None
        if qual and qual.lower() in SYSTEM_DBS:
            raise SqlRejected(f"database {qual} is not accessible")
        if qual is not None and qual != allowed_db:
            raise SqlRejected(f"database {qual} is outside this tenant")
        if qual is None and table.name.lower() in ctes:
            continue  # CTE reference, not a real table
        if tenant.mode == "column":
            _wrap_with_tenant_filter(table, allowed_db, tenant.column, tenant.value)
        else:
            table.set("db", exp.to_identifier(allowed_db, quoted=True))
    return Admitted(sql=tree.sql(dialect="clickhouse"))


def _wrap_with_tenant_filter(table: exp.Table, db: str, column: str, value: str) -> None:
    alias = table.alias or table.name
    inner_table = exp.Table(this=exp.to_identifier(table.name, quoted=True), db=exp.to_identifier(db, quoted=True))
    inner = (
        exp.select("*")
        .from_(inner_table)
        .where(exp.EQ(this=exp.column(column, quoted=True), expression=exp.Literal.string(value)))
    )
    table.replace(exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(alias, quoted=True))))
