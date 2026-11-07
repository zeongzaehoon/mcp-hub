"""infra/terraform must create the tables exactly as the code expects (schema.py is what dev/local creates)."""

import re
from pathlib import Path

from app.infra.dynamo.schema import LOG_TABLE, MAIN_TABLE

TF = Path(__file__).resolve().parents[2] / "infra/terraform"


def _table_block(text: str, resource: str) -> str:
    start = text.index(f'resource "aws_dynamodb_table" "{resource}"')
    nxt = text.find('resource "aws_dynamodb_table"', start + 1)
    return text[start: nxt if nxt != -1 else len(text)]


def _field(body: str, key: str) -> str:
    return re.search(rf'{key}\s*=\s*"([^"]+)"', body).group(1)


def _gsis(block: str) -> dict[str, tuple[str, str]]:
    out = {}
    for m in re.finditer(r'global_secondary_index \{(.*?)\n  \}', block, re.DOTALL):
        body = m.group(1)
        out[_field(body, "name")] = (_field(body, "hash_key"), _field(body, "range_key"))
    return out


def _expected(spec) -> dict[str, tuple[str, str]]:
    return {g["IndexName"]: tuple(k["AttributeName"] for k in g["KeySchema"]) for g in spec["GlobalSecondaryIndexes"]}


def test_terraform_tables_match_code_schema():
    text = (TF / "dynamodb.tf").read_text()
    for resource, spec in (("main", MAIN_TABLE), ("logs", LOG_TABLE)):
        block = _table_block(text, resource)
        assert re.search(r'hash_key\s*=\s*"PK"', block) and re.search(r'range_key\s*=\s*"SK"', block)
        assert _gsis(block) == _expected(spec), resource
        attrs = set(re.findall(r'attribute \{\s*name = "([^"]+)"', block))
        assert attrs == {a["AttributeName"] for a in spec["AttributeDefinitions"]}, resource
        assert 'billing_mode                = "PAY_PER_REQUEST"' in block
    assert re.search(r'ttl \{\s*attribute_name = "ttl"', _table_block(text, "logs"))  # services/logs.py writes `ttl`


def test_mcp_role_has_no_dynamodb_and_backend_only_appends_audit():
    iam = (TF / "iam.tf").read_text()
    mcp = iam[iam.index('data "aws_iam_policy_document" "mcp"'):iam.index('resource "aws_iam_policy" "backend"')]
    backend = iam[iam.index('data "aws_iam_policy_document" "backend"'):iam.index('data "aws_iam_policy_document" "mcp"')]
    assert "dynamodb:" not in mcp
    # backend: write-only to the audit lake (no read/list/delete => it cannot rewrite or erase history);
    # read-only (list + HEAD) under mcp/artifacts/ for the console artifact index — never Put/Delete there
    actions = {a for a in re.findall(r'"(s3:[A-Za-z*]+)"', backend) if a != "s3:prefix"}  # s3:prefix is the ListBucket condition key
    assert actions == {"s3:PutObject", "s3:ListBucket", "s3:GetObject"}
    assert sorted(re.findall(r'aws_s3_bucket\.data\.arn\}/([^"*]+)\*"', backend)) == ["audit/", "mcp/artifacts/"]
    put = re.search(r'sid\s*=\s*"AuditLakeAppendOnly".*?resources\s*=\s*\[([^\]]+)\]', backend, re.DOTALL).group(1)
    assert "audit/" in put and "mcp/" not in put
    get = re.search(r'sid\s*=\s*"ArtifactIndexHead".*?resources\s*=\s*\[([^\]]+)\]', backend, re.DOTALL).group(1)
    assert "mcp/artifacts/" in get and "audit/" not in get
    assert re.search(r'variable = "s3:prefix"\s*values\s*=\s*\["mcp/artifacts/\*"\]', backend)
    # MCP servers: their own roots only, never audit/
    assert "audit/" not in mcp and re.findall(r'for p in \[([^\]]+)\]', mcp) == ['"mcp", "control"']


def test_bucket_lifecycle_matches_layout():
    s3 = (TF / "s3.tf").read_text()
    prefixes = re.findall(r'prefix = "([^"]+)"', s3)
    assert prefixes == ["mcp/artifacts/", "mcp/exports/", "mcp/reports/", "audit/"]
    var = (TF / "variables.tf").read_text()
    assert re.search(r'variable "audit_retention_days" \{[^}]*default\s+=\s+365', var)  # = layout.AUDIT_RETENTION_DAYS
