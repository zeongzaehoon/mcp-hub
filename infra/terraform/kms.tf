# Wraps the tenant data key (TENANT_SECRET_KEY_ENC): DSNs and binding credentials are encrypted with it.
resource "aws_kms_key" "app_data" {
  description             = "mcp-cluster ${var.env} tenant secret envelope"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "app_data" {
  name          = "alias/mcp-cluster-${var.env}-app-data"
  target_key_id = aws_kms_key.app_data.key_id
}

# Server-side encryption of the bucket and the DynamoDB tables.
resource "aws_kms_key" "storage" {
  description             = "mcp-cluster ${var.env} storage (S3, DynamoDB)"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "storage" {
  name          = "alias/mcp-cluster-${var.env}-storage"
  target_key_id = aws_kms_key.storage.key_id
}
