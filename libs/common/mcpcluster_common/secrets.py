"""Must produce the same Fernet key as backend/app/core/security.py.

Order: KMS envelope (TENANT_SECRET_KEY_ENC) > plain TENANT_SECRET_KEY > dev-only derivation from JWT_SECRET.
The unwrapped key is cached per process; a configured envelope that cannot be unwrapped is fatal."""

import base64
import hashlib
import os
from functools import lru_cache

from cryptography.fernet import Fernet

from mcpcluster_common.settings import CommonSettings


KMS_CONTEXT = {"purpose": "tenant-secret-key", "app": "mcp-cluster"}  # = scripts/kms_wrap_key.py, IAM condition


@lru_cache(maxsize=4)
def _unwrap(ciphertext_b64: str, endpoint: str, region: str) -> bytes:
    import boto3

    kms = boto3.client("kms", region_name=region, endpoint_url=endpoint or None,
                       aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"), aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"))
    plain = kms.decrypt(CiphertextBlob=base64.b64decode(ciphertext_b64), EncryptionContext=KMS_CONTEXT)["Plaintext"]
    if len(plain) != 32:
        raise RuntimeError("TENANT_SECRET_KEY_ENC must wrap a 32-byte data key")
    return plain


def fernet(s: CommonSettings) -> Fernet:
    if s.tenant_secret_key_enc:
        return Fernet(base64.urlsafe_b64encode(_unwrap(s.tenant_secret_key_enc, s.kms_endpoint, s.s3_region)))
    if s.tenant_secret_key:
        return Fernet(s.tenant_secret_key.encode())
    if s.app_env != "dev":
        raise RuntimeError("TENANT_SECRET_KEY_ENC (or TENANT_SECRET_KEY) must be set outside dev")
    derived = hashlib.sha256(f"tenant:{s.jwt_secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def decrypt(s: CommonSettings, token: str) -> str:
    return fernet(s).decrypt(token.encode()).decode()
