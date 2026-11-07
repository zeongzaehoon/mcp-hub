"""S3-compatible object storage (MinIO in dev, AWS S3 / NCP Object Storage in prod) shared by export, artifacts and
monitors. One bucket (`ftg-mcp`); the key layout lives in layout.py."""

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from mcpcluster_common import layout
from mcpcluster_common.settings import CommonSettings


class ObjectStorage:
    def __init__(self, settings: CommonSettings):
        self.s = settings
        self._session = aioboto3.Session()
        self._bucket_ready = False

    def client(self, endpoint: str | None = None):
        return self._session.client(
            "s3", endpoint_url=endpoint or self.s.s3_endpoint, region_name=self.s.s3_region,
            aws_access_key_id=self.s.s3_access_key, aws_secret_access_key=self.s.s3_secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    async def ensure_bucket(self, c) -> None:
        if self._bucket_ready or not self.s.s3_manage_bucket:
            return  # IaC-managed bucket: the role only has object access on its prefixes
        try:
            await c.head_bucket(Bucket=self.s.s3_bucket)
        except ClientError:
            try:
                await c.create_bucket(Bucket=self.s.s3_bucket, CreateBucketConfiguration={"LocationConstraint": self.s.s3_region})
            except ClientError as e:
                if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    raise
        try:
            # artifacts are working material: expire them server-side too (read path also checks expires_at).
            # audit/ is written by the backend; its retention is set here because only this side manages the dev bucket
            await c.put_bucket_lifecycle_configuration(Bucket=self.s.s3_bucket, LifecycleConfiguration={"Rules": [
                {"ID": "artifacts-ttl", "Status": "Enabled", "Filter": {"Prefix": f"{layout.ARTIFACTS}/"},
                 "Expiration": {"Days": self.s.artifact_ttl_days}},
                {"ID": "audit-retention", "Status": "Enabled", "Filter": {"Prefix": "audit/"},
                 "Expiration": {"Days": layout.AUDIT_RETENTION_DAYS}},
            ]})
        except ClientError:
            pass  # storage without lifecycle support: expires_at on read still applies
        self._bucket_ready = True

    async def put_new(self, c, key: str, body: bytes, **kw) -> None:
        """Write-once (`If-None-Match: *`): mcp/ objects are never overwritten. Keys carry a random id, so a 412 means
        a bug or a replayed request, and it surfaces as an error instead of silently replacing someone's file."""
        await c.put_object(Bucket=self.s.s3_bucket, Key=key, Body=body, IfNoneMatch="*", **kw)
