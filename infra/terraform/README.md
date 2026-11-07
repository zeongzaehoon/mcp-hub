# infra/terraform — AWS resources for mcp-cluster

DynamoDB (`mcp_cluster`, `mcp_cluster_logs` + TTL), the object bucket (public access blocked, SSE-KMS, TLS-only,
lifecycle for `mcp/artifacts|exports|reports/` and `audit/` — layout: `docs/S3_LAYOUT.md`), two KMS keys (tenant key envelope, storage SSE) and one IAM policy
per component. The table shapes are pinned to `backend/app/infra/dynamo/schema.py` by `backend/tests/test_iac_contract.py`.

```bash
terraform init
terraform plan -var env=stg -var bucket_name=ftg-mcp-stg \
  -var backend_role_name=<ecs-task-role> -var mcp_role_name=<ecs-task-role>
```

After apply:
- `TENANT_SECRET_KEY_ENC=$(uv run --with boto3 python scripts/kms_wrap_key.py --key-id $(terraform output -raw app_data_key_alias))`
- MCP servers: `S3_BUCKET=$(terraform output -raw bucket)`, **`S3_MANAGE_BUCKET=0`** (the role has object access on its
  prefixes only; it may not create or configure the bucket), `ARTIFACT_TTL_DAYS` = `artifact_ttl_days`.
- Backend: the same `S3_BUCKET`, `S3_MANAGE_BUCKET=0`, no `S3_ENDPOINT`/`S3_ACCESS_KEY` (task role). It may only
  `PutObject` under `audit/` (access-log lake, `audit_retention_days` >= 365, Glacier IR after 90 days).
- The gateway needs no AWS permissions. NCP Object Storage is S3-compatible (point `S3_ENDPOINT` at it); this
  module targets AWS.

Validate locally: `docker run --rm -v "$PWD:/w" -w /w hashicorp/terraform:1.9 init -backend=false && ... validate`.
