# NCP (Naver Cloud Platform) — object storage for exports / artifacts / reports / monitors.
# The ncloud provider (v4.0.x) only creates buckets and ACLs, so lifecycle rules go through NCP Object Storage's
# S3-compatible API with the AWS provider pointed at the NCP endpoint. See README.md for what NCP cannot provide.
terraform {
  required_version = ">= 1.6"
  required_providers {
    ncloud = {
      source  = "NaverCloudPlatform/ncloud"
      version = "~> 4.0"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}

provider "ncloud" {
  region      = var.region
  support_vpc = true
  # credentials: NCLOUD_ACCESS_KEY / NCLOUD_SECRET_KEY environment variables
}

# S3-compatible control of the same bucket (lifecycle). Credentials: the same NCP API key as AWS_* env vars.
provider "aws" {
  alias                       = "ncp_s3"
  region                      = "us-east-1" # ignored by NCP; required by the provider
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_region_validation      = true
  skip_metadata_api_check     = true
  s3_use_path_style           = true
  endpoints {
    s3 = var.object_storage_endpoint
  }
}

resource "ncloud_objectstorage_bucket" "data" {
  bucket_name = var.bucket_name
}

resource "ncloud_objectstorage_bucket_acl" "data" {
  bucket_name = ncloud_objectstorage_bucket.data.bucket_name
  rule        = "private"
}

# Layout: docs/S3_LAYOUT.md. mcp/artifacts must match ARTIFACT_TTL_DAYS; control/ persists; audit/ kept audit_retention_days
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  provider = aws.ncp_s3
  bucket   = ncloud_objectstorage_bucket.data.bucket_name

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
    expiration {
      days = var.audit_retention_days
    }
  }
}
