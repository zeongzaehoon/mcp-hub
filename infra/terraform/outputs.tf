output "table_name" { value = aws_dynamodb_table.main.name }
output "log_table_name" { value = aws_dynamodb_table.logs.name }
output "bucket" { value = aws_s3_bucket.data.bucket }
output "app_data_key_alias" {
  value       = aws_kms_alias.app_data.name
  description = "Use with scripts/kms_wrap_key.py --key-id to create TENANT_SECRET_KEY_ENC"
}
output "backend_policy_arn" { value = aws_iam_policy.backend.arn }
output "mcp_policy_arn" { value = aws_iam_policy.mcp.arn }
