"""Create a new tenant data key under a KMS key and print the envelope for TENANT_SECRET_KEY_ENC.

    uv run --with boto3 python scripts/kms_wrap_key.py --key-id alias/ftg-app-data [--endpoint http://localhost:8002]

The plaintext key is never printed or written; only KMS can unwrap it. Changing the key makes existing encrypted
DSNs / binding credentials unreadable — re-save them (console) after switching, or keep the old key until then."""

import argparse
import base64

import boto3

ap = argparse.ArgumentParser()
ap.add_argument("--key-id", required=True)
ap.add_argument("--endpoint", default=None)
ap.add_argument("--region", default="ap-northeast-2")
a = ap.parse_args()
kms = boto3.client("kms", region_name=a.region, endpoint_url=a.endpoint)
# the context binds the envelope to this purpose; unwrap must present it and IAM requires it
CONTEXT = {"purpose": "tenant-secret-key", "app": "mcp-cluster"}
blob = kms.generate_data_key(KeyId=a.key_id, NumberOfBytes=32, EncryptionContext=CONTEXT)["CiphertextBlob"]
print(base64.b64encode(blob).decode())
