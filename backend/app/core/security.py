import base64
import hashlib
import secrets
import time
from functools import lru_cache
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet

from app.core.config import get_settings

_ph = PasswordHasher()

API_KEY_PREFIX = "mcpk"


def hash_password(raw: str) -> str:
    return _ph.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, raw)
    except VerifyMismatchError:
        return False


def generate_api_key() -> tuple[str, str, str]:
    """Returns (raw_key, key_hash, key_prefix). raw_key is shown to the user exactly once."""
    public = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    raw = f"{API_KEY_PREFIX}_{public}_{secret}"
    return raw, hash_api_key(raw), f"{API_KEY_PREFIX}_{public}"


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_jwt(claims: dict[str, Any]) -> str:
    s = get_settings()
    now = int(time.time())
    payload = {**claims, "iat": now, "exp": now + s.jwt_ttl_seconds}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_jwt(token: str) -> dict[str, Any]:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=["HS256"])


KMS_CONTEXT = {"purpose": "tenant-secret-key", "app": "mcp-cluster"}  # = scripts/kms_wrap_key.py, IAM condition


@lru_cache
def _fernet() -> Fernet:
    """Must produce the same key as libs/common/mcpcluster_common/secrets.py.

    Order: KMS envelope (TENANT_SECRET_KEY_ENC) > plain TENANT_SECRET_KEY > dev-only derivation from JWT_SECRET.
    A configured envelope that cannot be unwrapped is fatal — never fall back to a weaker key."""
    s = get_settings()
    if s.tenant_secret_key_enc:
        import boto3

        kms = boto3.client("kms", region_name=s.aws_region, endpoint_url=s.kms_endpoint,
                           aws_access_key_id=s.aws_access_key_id, aws_secret_access_key=s.aws_secret_access_key)
        plain = kms.decrypt(CiphertextBlob=base64.b64decode(s.tenant_secret_key_enc), EncryptionContext=KMS_CONTEXT)["Plaintext"]
        if len(plain) != 32:
            raise RuntimeError("TENANT_SECRET_KEY_ENC must wrap a 32-byte data key")
        return Fernet(base64.urlsafe_b64encode(plain))
    if s.tenant_secret_key:
        return Fernet(s.tenant_secret_key.encode())
    if s.app_env != "dev":
        raise RuntimeError("TENANT_SECRET_KEY_ENC (or TENANT_SECRET_KEY) must be set outside dev")
    derived = hashlib.sha256(f"tenant:{s.jwt_secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
