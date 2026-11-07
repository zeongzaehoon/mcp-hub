from functools import lru_cache
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "change-me-dev-jwt-secret"
DEV_MASTER_PASSWORD = "master-dev-password-123"
DEV_SSO_HANDOFF_SECRET = "change-me-dev-sso-handoff-secret-0123456789"


def _redis_url_has_password(url: str) -> bool:
    u = urlsplit(url)
    return bool(u.password) or bool(parse_qs(u.query).get("password"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = ""  # must be set explicitly; only "dev" may derive the tenant key from JWT_SECRET
    jwt_secret: str = DEV_JWT_SECRET
    jwt_ttl_seconds: int = 60 * 60 * 8
    tenant_secret_key: str = ""  # Fernet key; derived from jwt_secret in dev when empty
    # preferred outside dev: KMS envelope — base64 CiphertextBlob of a 32-byte data key (see scripts/kms_wrap_key.py)
    tenant_secret_key_enc: str = ""
    kms_endpoint: str | None = None  # None => AWS default endpoint; dev: http://local-kms:8080 (aws-local)

    master_email: str = "master@example.com"
    master_password: str = DEV_MASTER_PASSWORD

    aws_region: str = "ap-northeast-2"
    dynamo_endpoint: str | None = "http://localhost:8000"
    aws_access_key_id: str | None = None  # None => default credential chain (IAM role in prod)
    aws_secret_access_key: str | None = None
    table_name: str = "mcp_cluster"
    log_table_name: str = "mcp_cluster_logs"

    redis_url: str = "redis://localhost:6379"
    redis_password: str = Field("", repr=False)  # single source: env/redis.env; merged into redis_url
    redis_db_auth: int = 1
    redis_db_hot: int = 2
    redis_db_logs: int = 3

    outbox_poll_seconds: float = 2.0
    reconcile_interval_seconds: float = 300.0
    log_consumer_batch: int = 200
    log_ttl_days: int = 90  # DynamoDB (console); the S3 audit lake keeps access logs >= 1 year

    # S3 audit lake (`ftg-mcp` bucket, audit/ only — see app/infra/s3/audit.py). No key => default credential chain
    s3_endpoint: str | None = None
    s3_bucket: str = "ftg-mcp"
    s3_access_key: str | None = None
    s3_secret_key: str | None = Field(None, repr=False)
    s3_manage_bucket: bool = True  # dev: create the bucket if missing. 0 when terraform owns it (the role cannot)

    cors_origins: str = "http://localhost:5173"

    # SSO (company OIDC providers). The browser returns to the console / the MCP gateway at these public URLs;
    # the gateway swaps a one-time handoff code for a session key server-to-server with the shared secret.
    console_url: str = "http://localhost:19000"
    gateway_public_url: str = "http://localhost:19000"
    sso_handoff_secret: str = Field("", repr=False)  # empty => MCP-connector SSO disabled (console SSO still works)
    sso_session_key_days: int = 30

    # Platform Google OAuth client (one for the whole deployment): the connector SSO of Workspace companies signs
    # employees in through it (openid/email/profile only), so a company needs no OAuth app of its own. Empty client id
    # => companies must register their own OIDC provider for connector SSO. Dev/e2e point the issuer at the mock.
    google_client_id: str = ""
    google_client_secret: str = Field("", repr=False)
    google_issuer: str = "https://accounts.google.com"
    # Directory sync runs as a service account with domain-wide delegation (key registered by an operator per company).
    # The token URL is this setting, never the `token_uri` inside an uploaded key file.
    google_directory_url: str = "https://admin.googleapis.com"
    google_token_url: str = "https://oauth2.googleapis.com/token"
    # Directory sync (users + groups -> users + teams): every company with an active connection, this often.
    workspace_sync_interval_s: float = 3600.0
    workspace_sync_on_connect: bool = True  # kick a sync right after an operator registers the key (tests: off)

    # Console dashboards (read-only). Global counts come from a full entity scan cached in Redis db2 this often;
    # company counts are one partition query per request. 24h call metrics are hourly Redis buckets the log consumer
    # fills (services/metrics.py). Health: the backend GETs <internal url>/healthz per MCP service — the internal
    # URLs come from env only, never from the server record's public `endpoint` (an admin-editable URL is not a
    # fetch target). Empty => that type is not checked.
    stats_refresh_s: float = 300.0
    health_check_interval_s: float = 60.0
    health_check_timeout_s: float = 3.0
    mcp_internal_postgres: str = ""
    mcp_internal_clickhouse: str = ""
    mcp_internal_mongo: str = ""
    mcp_internal_hacker: str = ""
    mcp_internal_gateway: str = ""

    @model_validator(mode="after")
    def _merge_redis_password(self) -> "Settings":
        if self.redis_password and not _redis_url_has_password(self.redis_url):
            u = urlsplit(self.redis_url)
            user, _, host = u.netloc.rpartition("@")  # an ACL username without password is kept
            self.redis_url = urlunsplit(u._replace(netloc=f"{user}:{quote(self.redis_password, safe='')}@{host}"))
        return self

    def runtime_problems(self) -> list[str]:
        """Outside dev the dev defaults are forgeable (JWT) or public (master password), and Redis db1 is the whole
        authorization source — each is a startup error."""
        if self.app_env == "dev":
            return []
        problems = []
        if self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret.encode()) < 32:
            problems.append("JWT_SECRET must be a non-default value of at least 32 bytes")
        if self.master_password == DEV_MASTER_PASSWORD or len(self.master_password) < 12:
            problems.append("MASTER_PASSWORD must be a non-default value of at least 12 characters")
        if not _redis_url_has_password(self.redis_url):
            problems.append("REDIS_PASSWORD (or credentials in REDIS_URL) must be set")
        if self.sso_handoff_secret and (self.sso_handoff_secret == DEV_SSO_HANDOFF_SECRET or len(self.sso_handoff_secret) < 32):
            problems.append("SSO_HANDOFF_SECRET must be a non-default value of at least 32 characters (or empty to disable)")
        if self.google_client_id:
            if not self.google_client_secret:
                problems.append("GOOGLE_CLIENT_SECRET must be set with GOOGLE_CLIENT_ID")
            if not self.google_issuer.startswith("https://"):
                problems.append("GOOGLE_ISSUER must be https")
        if not self.google_directory_url.startswith("https://") or not self.google_token_url.startswith("https://"):
            problems.append("GOOGLE_DIRECTORY_URL / GOOGLE_TOKEN_URL must be https")
        return problems

    def internal_urls(self) -> dict[str, str]:
        """{server_type|'gateway': base url} for the configured health targets."""
        pairs = (("postgres", self.mcp_internal_postgres), ("clickhouse", self.mcp_internal_clickhouse),
                 ("mongo", self.mcp_internal_mongo), ("hacker", self.mcp_internal_hacker), ("gateway", self.mcp_internal_gateway))
        return {k: v.rstrip("/") for k, v in pairs if v}

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def sso_allow_http_issuer(self) -> bool:
        """Only dev may talk to a plain-http provider (the mock IdP on the compose network)."""
        return self.app_env == "dev"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
