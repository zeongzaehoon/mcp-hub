from urllib.parse import urlsplit

from app.core.errors import InvariantViolation, NotFound
from app.core.security import encrypt_secret
from app.domain.enums import ServerType, Status
from app.domain.models import McpServer
from app.infra.dynamo.keys import server_pk
from app.services.base import ServiceBase
from app.services.companies import assert_transition

DSN_SCHEMES: dict[ServerType, set[str]] = {
    ServerType.POSTGRES: {"postgres", "postgresql"},
    ServerType.HACKER: {"postgres", "postgresql"},
    ServerType.CLICKHOUSE: {"clickhouse", "clickhouses", "http", "https"},
    ServerType.MONGO: {"mongodb", "mongodb+srv"},
}


def validate_dsn(server_type: ServerType, dsn: str) -> str:
    dsn = dsn.strip()
    parts = urlsplit(dsn)
    if parts.scheme not in DSN_SCHEMES[server_type] or not parts.netloc:
        raise InvariantViolation(f"DSN for {server_type} must use one of {sorted(DSN_SCHEMES[server_type])}://host[:port]/…", code="bad_dsn")
    return dsn


class ServerService(ServiceBase):
    async def get(self, server_id: str) -> McpServer:
        e = await self.repo.get(server_pk(server_id), "META")
        if not isinstance(e, McpServer):
            raise NotFound(f"server {server_id} not found")
        return e

    async def create(self, *, server_type: ServerType, name: str, description: str, endpoint: str, dsn: str | None = None) -> McpServer:
        enc = encrypt_secret(validate_dsn(server_type, dsn)) if dsn else None
        s = McpServer(server_type=server_type, name=name, description=description, endpoint=endpoint, connection_enc=enc)
        await self.commit([s], [self.repo.tx.create(s)])
        return s

    async def list(self, *, limit: int, cursor: str | None, status: Status | None, server_type: ServerType | None):
        filters = {}
        if status:
            filters["status"] = status
        if server_type:
            filters["server_type"] = server_type
        items, nxt = await self.repo.query("TYPE#SERVER", index="gsi1", limit=limit, cursor=cursor, filters=filters or None)
        return [i for i in items if isinstance(i, McpServer)], nxt

    async def update(self, server_id: str, *, name=None, description=None, endpoint=None, status: Status | None = None, dsn: str | None = None, clear_dsn: bool = False) -> McpServer:
        s = await self.get(server_id)
        if clear_dsn:
            s.connection_enc = None
        elif dsn:
            s.connection_enc = encrypt_secret(validate_dsn(s.server_type, dsn))
        if name is not None:
            s.name = name
        if description is not None:
            s.description = description
        if endpoint is not None:
            s.endpoint = endpoint
        if status is not None and status != s.status:
            assert_transition(s.status, status)
            s.status = status
        expected = self.bump(s)
        await self.commit([s], [self.repo.tx.replace(s, expected)])
        return s
