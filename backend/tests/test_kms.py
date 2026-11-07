"""KMS envelope for the tenant secret key (aws-local local-kms on localhost:8002). Skips when KMS is down.
Backend and MCP servers must derive the same key: both are pinned to Fernet(urlsafe_b64(plaintext data key))
(this file for the backend, mcp-postgres/tests/test_kms.py for mcpcluster_common)."""

import base64
import os

import boto3
import pytest
from cryptography.fernet import Fernet

KMS = os.environ.get("TEST_KMS_ENDPOINT", "http://localhost:8002")
KEY_ID = "alias/ftg-app-data"


def _kms():
    return boto3.client("kms", region_name="ap-northeast-2", endpoint_url=KMS, aws_access_key_id="test", aws_secret_access_key="test")


def _up() -> bool:
    try:
        _kms().list_keys(Limit=1)
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _up(), reason="local KMS not reachable")


@pytest.fixture
def envelope(monkeypatch):
    from app.core import config, security

    dk = _kms().generate_data_key(KeyId=KEY_ID, NumberOfBytes=32, EncryptionContext={"purpose": "tenant-secret-key", "app": "mcp-cluster"})
    blob = base64.b64encode(dk["CiphertextBlob"]).decode()
    monkeypatch.setenv("TENANT_SECRET_KEY_ENC", blob)
    monkeypatch.setenv("KMS_ENDPOINT", KMS)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    config.get_settings.cache_clear()
    security._fernet.cache_clear()
    yield blob, dk["Plaintext"]
    config.get_settings.cache_clear()
    security._fernet.cache_clear()


def test_backend_unwraps_the_envelope(envelope):
    from app.core.security import decrypt_secret, encrypt_secret

    _, plain = envelope
    token = encrypt_secret("postgresql://u:s3cret@h/db")
    assert Fernet(base64.urlsafe_b64encode(plain)).decrypt(token.encode()) == b"postgresql://u:s3cret@h/db"
    assert decrypt_secret(token) == "postgresql://u:s3cret@h/db"


def test_bad_envelope_is_fatal_not_a_fallback(monkeypatch):
    from app.core import config, security

    monkeypatch.setenv("TENANT_SECRET_KEY_ENC", base64.b64encode(b"not-a-kms-blob" * 4).decode())
    monkeypatch.setenv("TENANT_SECRET_KEY", Fernet.generate_key().decode())  # present, but must NOT be used instead
    monkeypatch.setenv("KMS_ENDPOINT", KMS)
    config.get_settings.cache_clear()
    security._fernet.cache_clear()
    try:
        with pytest.raises(Exception):  # noqa: B017 - botocore raises InvalidCiphertextException
            security.encrypt_secret("x")
    finally:
        config.get_settings.cache_clear()
        security._fernet.cache_clear()


def test_outside_dev_without_any_key_refuses_to_start(monkeypatch):
    from app.core import config, security

    for k in ("TENANT_SECRET_KEY_ENC", "TENANT_SECRET_KEY"):
        monkeypatch.setenv(k, "")
    monkeypatch.setenv("APP_ENV", "prod")
    config.get_settings.cache_clear()
    security._fernet.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="must be set outside dev"):
            security.encrypt_secret("x")
    finally:
        config.get_settings.cache_clear()
        security._fernet.cache_clear()
