import logging
import os
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

log = logging.getLogger(__name__)


def with_redis_password(url: str, password: str) -> str:
    """REDIS_PASSWORD (single source: env/redis.env) into REDIS_URL, unless the URL already carries one."""
    if not password or redis_url_has_password(url):
        return url
    u = urlsplit(url)
    user, _, host = u.netloc.rpartition("@")  # an ACL username without password is kept
    return urlunsplit(u._replace(netloc=f"{user}:{quote(password, safe='')}@{host}"))


def redis_url_has_password(url: str) -> bool:
    u = urlsplit(url)
    return bool(u.password) or bool(parse_qs(u.query).get("password"))


@dataclass(frozen=True)
class CommonSettings:
    redis_url: str = field(default_factory=lambda: os.environ.get("REDIS_URL", "redis://localhost:6379"))
    redis_password: str = field(default_factory=lambda: os.environ.get("REDIS_PASSWORD", ""), repr=False)
    redis_db_auth: int = field(default_factory=lambda: int(os.environ.get("REDIS_DB_AUTH", "1")))
    redis_db_hot: int = field(default_factory=lambda: int(os.environ.get("REDIS_DB_HOT", "2")))
    redis_db_logs: int = field(default_factory=lambda: int(os.environ.get("REDIS_DB_LOGS", "3")))
    server_id: str = field(default_factory=lambda: os.environ.get("SERVER_ID", ""))  # optional pin
    server_type: str = field(default_factory=lambda: os.environ.get("SERVER_TYPE", ""))
    app_env: str = field(default_factory=lambda: os.environ.get("APP_ENV", ""))  # unset is NOT dev
    jwt_secret: str = field(default_factory=lambda: os.environ.get("JWT_SECRET", "change-me-dev-jwt-secret"))
    tenant_secret_key: str = field(default_factory=lambda: os.environ.get("TENANT_SECRET_KEY", ""))
    tenant_secret_key_enc: str = field(default_factory=lambda: os.environ.get("TENANT_SECRET_KEY_ENC", ""))  # KMS envelope
    kms_endpoint: str = field(default_factory=lambda: os.environ.get("KMS_ENDPOINT", ""))
    rate_limit_default_per_minute: int = field(default_factory=lambda: int(os.environ.get("RATE_LIMIT_PER_MINUTE", "600")))  # 0 disables
    # exports (MinIO / S3): small files inline, large ones uploaded + presigned link
    s3_endpoint: str = field(default_factory=lambda: os.environ.get("S3_ENDPOINT", "http://localhost:9000"))
    s3_public_endpoint: str = field(default_factory=lambda: os.environ.get("S3_PUBLIC_ENDPOINT", os.environ.get("S3_ENDPOINT", "http://localhost:9000")))
    s3_bucket: str = field(default_factory=lambda: os.environ.get("S3_BUCKET", "ftg-mcp"))
    # 1 (dev): create the bucket + lifecycle on first use. 0 (IaC-managed, least-privilege role): never touch bucket config
    s3_manage_bucket: bool = field(default_factory=lambda: os.environ.get("S3_MANAGE_BUCKET", "1") == "1")
    s3_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "ap-northeast-2"))
    s3_access_key: str = field(default_factory=lambda: os.environ.get("S3_ACCESS_KEY", "minioadmin"))
    s3_secret_key: str = field(default_factory=lambda: os.environ.get("S3_SECRET_KEY", "minioadmin123"))
    export_inline_max_bytes: int = field(default_factory=lambda: int(os.environ.get("EXPORT_INLINE_MAX_BYTES", "2000000")))
    export_url_ttl_s: int = field(default_factory=lambda: int(os.environ.get("EXPORT_URL_TTL_S", "900")))
    export_max_rows: int = field(default_factory=lambda: int(os.environ.get("EXPORT_MAX_ROWS", "500000")))
    # compute: below this many rows a Python-list copy is cheaper; at/above it arrays are handed to Rust
    # through the Arrow C Data Interface (no per-element Python objects)
    compute_arrow_threshold_rows: int = field(default_factory=lambda: int(os.environ.get("COMPUTE_ARROW_THRESHOLD_ROWS", "20000")))
    artifact_ttl_days: int = field(default_factory=lambda: int(os.environ.get("ARTIFACT_TTL_DAYS", "7")))
    artifact_max_bytes: int = field(default_factory=lambda: int(os.environ.get("ARTIFACT_MAX_BYTES", "5000000")))
    # monitors: scheduler tick and the shortest allowed interval (0 tick disables the scheduler)
    monitor_tick_s: float = field(default_factory=lambda: float(os.environ.get("MONITOR_TICK_S", "15")))
    monitor_min_interval_s: int = field(default_factory=lambda: int(os.environ.get("MONITOR_MIN_INTERVAL_S", "60")))
    monitor_max_per_user: int = field(default_factory=lambda: int(os.environ.get("MONITOR_MAX_PER_USER", "20")))  # per server type
    monitor_concurrency: int = field(default_factory=lambda: int(os.environ.get("MONITOR_CONCURRENCY", "8")))
    monitor_run_timeout_s: float = field(default_factory=lambda: float(os.environ.get("MONITOR_RUN_TIMEOUT_S", "30")))
    monitor_max_rows: int = field(default_factory=lambda: int(os.environ.get("MONITOR_MAX_ROWS", "100000")))
    log_stream: str = "logs:access"
    log_stream_maxlen: int = 100_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "redis_url", with_redis_password(self.redis_url, self.redis_password))

    def ensure_safe_runtime(self) -> None:
        """Called first in every server's main(). Redis db1 is the whole authorization source, so outside dev an
        unauthenticated Redis is a startup error, not a warning."""
        if self.app_env == "dev":
            return
        if not redis_url_has_password(self.redis_url):
            raise SystemExit("unsafe configuration: REDIS_PASSWORD (or credentials in REDIS_URL) must be set outside dev")
        if urlsplit(self.redis_url).scheme != "rediss":
            log.warning("REDIS_URL is not TLS (rediss://); use TLS unless Redis is on a private network")
