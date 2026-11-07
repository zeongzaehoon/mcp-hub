import aioboto3
from botocore.config import Config

from app.core.config import get_settings


class DynamoClientFactory:
    """Holds one aioboto3 session; `client()` yields a context-managed low-level client."""

    def __init__(self) -> None:
        self._session = aioboto3.Session()
        self._settings = get_settings()

    def client(self):
        s = self._settings
        creds = {}
        if s.dynamo_endpoint:  # local endpoint: use dummy static creds unless real ones are set
            creds = {"aws_access_key_id": s.aws_access_key_id or "local", "aws_secret_access_key": s.aws_secret_access_key or "local"}
        elif s.aws_access_key_id:
            creds = {"aws_access_key_id": s.aws_access_key_id, "aws_secret_access_key": s.aws_secret_access_key}
        return self._session.client(
            "dynamodb",
            region_name=s.aws_region,
            endpoint_url=s.dynamo_endpoint or None,
            **creds,
            config=Config(retries={"max_attempts": 5, "mode": "standard"}, max_pool_connections=64),
        )
