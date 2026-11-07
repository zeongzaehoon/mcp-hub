variable "region" {
  type    = string
  default = "KR"
}

variable "bucket_name" {
  type = string
}

variable "object_storage_endpoint" {
  type    = string
  default = "https://kr.object.ncloudstorage.com"
}

variable "artifact_ttl_days" {
  type    = number
  default = 7
}

variable "export_ttl_days" {
  type    = number
  default = 7
}

variable "report_ttl_days" {
  type    = number
  default = 30
}

variable "audit_retention_days" {
  type        = number
  default     = 365
  description = "Access-log lake (audit/) retention: at least 1 year (개인정보의 안전성 확보조치 기준 §8). Same as layout.AUDIT_RETENTION_DAYS"
  validation {
    condition     = var.audit_retention_days >= 365
    error_message = "access logs must be kept at least 365 days"
  }
}
