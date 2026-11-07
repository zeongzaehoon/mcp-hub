# infra/terraform-ncp — NCP object storage for mcp-cluster

Creates the object bucket (private ACL) and its lifecycle rules (`mcp/artifacts|exports|reports/` TTL, `audit/`
`audit_retention_days` >= 365; `control/` persists — layout: `docs/S3_LAYOUT.md`). The MCP servers talk to NCP Object Storage through its S3-compatible API:
`S3_ENDPOINT=https://kr.object.ncloudstorage.com`, `S3_BUCKET=<bucket>`, **`S3_MANAGE_BUCKET=0`**.

```bash
export NCLOUD_ACCESS_KEY=... NCLOUD_SECRET_KEY=...            # ncloud provider
export AWS_ACCESS_KEY_ID=$NCLOUD_ACCESS_KEY AWS_SECRET_ACCESS_KEY=$NCLOUD_SECRET_KEY   # S3-compatible lifecycle
terraform init && terraform plan -var bucket_name=ftg-mcp-<env>
```

## What NCP cannot provide through this module (verified against ncloud provider v4.0.7's schema, 2026-09-24)
| Need | AWS module | NCP status |
|---|---|---|
| Bucket lifecycle | `aws_s3_bucket_lifecycle_configuration` | via S3-compatible API (this module) |
| Server-side encryption, public-access block, TLS-only policy | resources | not in the ncloud provider — configure in the NCP console / S3 API; verify before go-live |
| Least-privilege access per component | IAM policies | Sub Account policies are not in the provider — create manually: MCP servers need object access on `mcp/` and `control/` only; backend needs PutObject on `audit/` only |
| **Tenant key envelope (KMS)** | AWS KMS `GenerateDataKey`/`Decrypt` | NCP Key Management Service has a **different API** — our envelope code speaks AWS KMS. On NCP use a plain `TENANT_SECRET_KEY` from NCP Secret Manager, or keep AWS KMS |
| **Control-plane database** | DynamoDB | **NCP has no DynamoDB equivalent.** The backend is written against DynamoDB (transactions, ConditionCheck). Open decision — see `history/loop/backlog.md` T34 |
| **Write-once objects** (`If-None-Match: *` on PutObject) | supported by AWS S3 | **unverified on NCP Object Storage** — `mcp/` uploads and the audit lake send it; test before go-live |
