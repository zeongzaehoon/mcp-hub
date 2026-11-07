variable "env" {
  type        = string
  description = "Environment name (stg, prod, ...)"
}

variable "region" {
  type    = string
  default = "ap-northeast-2"
}

variable "table_name" {
  type    = string
  default = "mcp_cluster"
}

variable "log_table_name" {
  type    = string
  default = "mcp_cluster_logs"
}

variable "bucket_name" {
  type        = string
  description = "The ftg-mcp bucket (mcp/, control/, audit/ — docs/S3_LAYOUT.md); globally unique, e.g. ftg-mcp-<env>"
}

variable "artifact_ttl_days" {
  type        = number
  default     = 7
  description = "Must match ARTIFACT_TTL_DAYS of the MCP servers"
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

variable "backend_role_name" {
  type        = string
  default     = ""
  description = "Existing IAM role (e.g. ECS task role) to attach the backend policy to; empty = policy only"
}

variable "mcp_role_name" {
  type        = string
  default     = ""
  description = "Existing IAM role for the MCP servers (all datasource types); empty = policy only"
}
