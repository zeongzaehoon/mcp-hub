output "bucket" {
  value = ncloud_objectstorage_bucket.data.bucket_name
}

output "s3_endpoint" {
  value       = var.object_storage_endpoint
  description = "S3_ENDPOINT / S3_PUBLIC_ENDPOINT for the MCP servers (run them with S3_MANAGE_BUCKET=0)"
}
