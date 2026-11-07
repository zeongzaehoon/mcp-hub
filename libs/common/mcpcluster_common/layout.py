"""Object layout of the `ftg-mcp` bucket — the one place S3 keys are built. Contract: docs/S3_LAYOUT.md (if the two
disagree, this file is right and the document is fixed).

Same conventions as the data-engineering lake (~/dev/data-engineering/S3_LAYOUT.md): dataset first, then Hive-style
`key=value` partitions, and three roots only:
  mcp/      user output — artifacts, exports, reports. Written once (If-None-Match), expired by lifecycle
  control/  persistent settings — monitors, monitor_state. Last write wins (the documented overwrite exception)
  audit/    access-log lake, parquet, append-only (written by the backend, see backend/app/infra/s3/audit.py)

Partition values are ids. One that could change the path (`/`, `=`, `..`, empty) is refused, never rewritten."""

import re
from datetime import UTC, datetime

ROOTS = ("mcp", "control", "audit")
ARTIFACTS = "mcp/artifacts"
EXPORTS = "mcp/exports"
REPORTS = "mcp/reports"
MONITORS = "control/monitors"
MONITOR_STATE = "control/monitor_state"
ACCESS_LOGS = "audit/access_logs"
# access logs must be kept at least a year (개인정보의 안전성 확보조치 기준 §8); same value as terraform audit_retention_days
AUDIT_RETENTION_DAYS = 365

_PART = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class LayoutError(ValueError):
    pass


def _id(name: str, value: str) -> str:
    if not isinstance(value, str) or not _PART.match(value):
        raise LayoutError(f"invalid {name}")
    return value


def part(name: str, value: str) -> str:
    return f"{name}={_id(name, value)}"


def dt(ts: datetime) -> str:
    return f"dt={ts.astimezone(UTC):%Y%m%d}"


def owner_prefix(dataset: str, company_id: str, user_id: str) -> str:
    return f"{dataset}/{part('tenant', company_id)}/{part('user', user_id)}/"


def artifact_key(company_id: str, user_id: str, artifact_id: str) -> str:
    # no dt: artifacts are read back by id alone
    return f"{owner_prefix(ARTIFACTS, company_id, user_id)}{_id('artifact_id', artifact_id)}.json"


def file_key(dataset: str, company_id: str, user_id: str, at: datetime, file_id: str, filename: str) -> str:
    """exports / reports. `filename` is already reduced to [A-Za-z0-9._-] (export.safe_filename)."""
    if dataset not in (EXPORTS, REPORTS) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}", filename or ""):
        raise LayoutError("invalid file key")
    return f"{owner_prefix(dataset, company_id, user_id)}{dt(at)}/{_id('file_id', file_id)}/{filename}"


def monitor_prefix(server_type: str, company_id: str | None = None, user_id: str | None = None) -> str:
    # server_type first: each server's scheduler lists only its own type
    p = f"{MONITORS}/{part('server_type', server_type)}/"
    if company_id is not None:
        p += f"{part('tenant', company_id)}/{part('user', user_id or '')}/"
    return p


def monitor_key(server_type: str, company_id: str, user_id: str, monitor_id: str) -> str:
    return f"{monitor_prefix(server_type, company_id, user_id)}{_id('monitor_id', monitor_id)}.json"


def monitor_state_key(company_id: str, user_id: str, monitor_id: str) -> str:
    return f"{owner_prefix(MONITOR_STATE, company_id, user_id)}{_id('monitor_id', monitor_id)}.json"
