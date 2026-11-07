# Least privilege per component. The MCP servers never touch DynamoDB (they read Redis projections only) and only
# the object prefixes they own (mcp/, control/); the backend may only append to the audit lake (PutObject on audit/:
# no read, list or delete). The gateway needs no AWS access at all. Layout: docs/S3_LAYOUT.md.

data "aws_iam_policy_document" "backend" {
  statement {
    sid = "SingleTableAndLogs"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:UpdateItem",
      "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchWriteItem", "dynamodb:ConditionCheckItem",
      "dynamodb:DescribeTable", "dynamodb:DescribeTimeToLive",
    ]
    resources = [
      aws_dynamodb_table.main.arn, "${aws_dynamodb_table.main.arn}/index/*",
      aws_dynamodb_table.logs.arn, "${aws_dynamodb_table.logs.arn}/index/*",
    ]
  }
  statement {
    sid       = "TenantKeyEnvelope"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.app_data.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:purpose"
      values   = ["tenant-secret-key"]
    }
  }
  statement {
    sid       = "AuditLakeAppendOnly"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/audit/*"]
  }
  # console artifact index: list + HEAD under mcp/artifacts/ only (metadata; the console never serves payloads)
  statement {
    sid       = "ArtifactIndexList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["mcp/artifacts/*"]
    }
  }
  statement {
    sid       = "ArtifactIndexHead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/mcp/artifacts/*"]
  }
  statement {
    sid       = "AuditLakeEncryption"
    actions   = ["kms:GenerateDataKey"]
    resources = [aws_kms_key.storage.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
  }
  statement {
    sid       = "TableEncryption"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [aws_kms_key.storage.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["dynamodb.${var.region}.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "mcp" {
  statement {
    sid       = "OwnPrefixesOnly"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [for p in ["mcp", "control"] : "${aws_s3_bucket.data.arn}/${p}/*"]
  }
  statement {
    sid       = "ListOwnPrefixes"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["mcp/artifacts/*", "control/monitors/*"]
    }
  }
  statement {
    sid       = "TenantKeyEnvelope"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.app_data.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:purpose"
      values   = ["tenant-secret-key"]
    }
  }
  statement {
    sid       = "BucketEncryption"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.storage.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "backend" {
  name   = "mcp-cluster-${var.env}-backend"
  policy = data.aws_iam_policy_document.backend.json
}

resource "aws_iam_policy" "mcp" {
  name   = "mcp-cluster-${var.env}-mcp"
  policy = data.aws_iam_policy_document.mcp.json
}

resource "aws_iam_role_policy_attachment" "backend" {
  count      = var.backend_role_name == "" ? 0 : 1
  role       = var.backend_role_name
  policy_arn = aws_iam_policy.backend.arn
}

resource "aws_iam_role_policy_attachment" "mcp" {
  count      = var.mcp_role_name == "" ? 0 : 1
  role       = var.mcp_role_name
  policy_arn = aws_iam_policy.mcp.arn
}
