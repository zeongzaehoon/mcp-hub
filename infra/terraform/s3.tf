resource "aws_s3_bucket" "data" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.storage.arn
    }
    bucket_key_enabled = true
  }
}

# Layout: docs/S3_LAYOUT.md. mcp/artifacts must expire like ARTIFACT_TTL_DAYS (reads also check expires_at);
# control/ (monitors) persists; audit/ (access-log lake) is kept audit_retention_days, cold after 90 days.
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "artifacts-ttl"
    status = "Enabled"
    filter {
      prefix = "mcp/artifacts/"
    }
    expiration {
      days = var.artifact_ttl_days
    }
  }
  rule {
    id     = "exports-ttl"
    status = "Enabled"
    filter {
      prefix = "mcp/exports/"
    }
    expiration {
      days = var.export_ttl_days
    }
  }
  rule {
    id     = "reports-ttl"
    status = "Enabled"
    filter {
      prefix = "mcp/reports/"
    }
    expiration {
      days = var.report_ttl_days
    }
  }
  rule {
    id     = "audit-retention"
    status = "Enabled"
    filter {
      prefix = "audit/"
    }
    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }
    expiration {
      days = var.audit_retention_days
    }
  }
}

data "aws_iam_policy_document" "bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id
  policy = data.aws_iam_policy_document.bucket.json
}
