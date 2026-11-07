"""mcpcluster_common unwraps the same KMS envelope to the same Fernet key as the backend
(both pinned to Fernet(urlsafe_b64(plaintext data key)); see backend/tests/test_kms.py). Skips without local KMS."""

import base64
import os

import boto3
import pytest
from cryptography.fernet import Fernet

from mcpcluster_common import secrets
from mcpcluster_common.settings import CommonSettings

KMS = os.environ.get("TEST_KMS_ENDPOINT", "http://localhost:8002")


def _kms():
    return boto3.client("kms", region_name="ap-northeast-2", endpoint_url=KMS, aws_access_key_id="test", aws_secret_access_key="test")


def _up() -> bool:
    try:
        _kms().list_keys(Limit=1)
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _up(), reason="local KMS not reachable")


def test_envelope_unwraps_to_the_shared_key(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    dk = _kms().generate_data_key(KeyId="alias/ftg-app-data", NumberOfBytes=32, EncryptionContext={"purpose": "tenant-secret-key", "app": "mcp-cluster"})
    s = CommonSettings(app_env="prod", tenant_secret_key_enc=base64.b64encode(dk["CiphertextBlob"]).decode(), kms_endpoint=KMS,
                       tenant_secret_key=Fernet.generate_key().decode())  # plain key present but the envelope wins
    token = Fernet(base64.urlsafe_b64encode(dk["Plaintext"])).encrypt(b"dsn").decode()  # what the backend would store
    secrets._unwrap.cache_clear()
    assert secrets.decrypt(s, token) == "dsn"


def test_bad_envelope_is_fatal(monkeypatch):
    s = CommonSettings(app_env="prod", tenant_secret_key_enc=base64.b64encode(b"junk" * 16).decode(), kms_endpoint=KMS)
    secrets._unwrap.cache_clear()
    with pytest.raises(Exception):  # noqa: B017
        secrets.fernet(s)


def test_envelope_without_the_purpose_context_is_refused(monkeypatch):
    # security review 2 #7: an envelope made for another purpose (no/other EncryptionContext) must not unwrap
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    blob = _kms().generate_data_key(KeyId="alias/ftg-app-data", NumberOfBytes=32)["CiphertextBlob"]
    s = CommonSettings(app_env="prod", tenant_secret_key_enc=base64.b64encode(blob).decode(), kms_endpoint=KMS)
    secrets._unwrap.cache_clear()
    with pytest.raises(Exception):  # noqa: B017 - InvalidCiphertextException
        secrets.fernet(s)
