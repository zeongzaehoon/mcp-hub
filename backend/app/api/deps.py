from dataclasses import dataclass
from typing import Annotated

import httpx
import jwt
from fastapi import Depends, Header, Request

from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound, Unauthorized
from app.core.google_directory import GoogleDirectory
from app.core.oidc import OidcClient
from app.core.security import decode_jwt
from app.domain.enums import Role, Status
from app.infra.dynamo.client import DynamoClientFactory
from app.infra.dynamo.repository import Repository
from app.infra.redis.client import RedisPools
from app.infra.redis.sync import RedisSync
from app.infra.s3.artifacts import ArtifactIndex
from app.services.access import AccessService
from app.services.apikeys import ApiKeyService
from app.services.auth import AuthService, Principal
from app.services.bindings import BindingService
from app.services.companies import CompanyService
from app.services.health import HealthChecker
from app.services.logs import AccessLogConsumer
from app.services.metrics import MetricsService
from app.services.outbox import Outbox
from app.services.policies import PolicyService
from app.services.reconciler import Reconciler
from app.services.servers import ServerService
from app.services.sso import SsoService
from app.services.stats import StatsService
from app.services.users import UserService
from app.services.workspace import WorkspaceService


@dataclass
class Container:
    repo: Repository
    pools: RedisPools
    sync: RedisSync
    oidc: OidcClient  # one relying-party client (discovery/JWKS caches) for the process
    health: HealthChecker  # MCP service liveness (one http client + Redis snapshot)

    @classmethod
    def build(cls) -> "Container":
        s = get_settings()
        repo = Repository(DynamoClientFactory())
        pools = RedisPools()
        oidc = OidcClient(httpx.AsyncClient(follow_redirects=False), allow_http=s.sso_allow_http_issuer)
        return cls(repo=repo, pools=pools, sync=RedisSync(pools), oidc=oidc, health=HealthChecker(pools.hot, s))

    @property
    def companies(self) -> CompanyService:
        return CompanyService(self.repo, self.sync)

    @property
    def users(self) -> UserService:
        return UserService(self.repo, self.sync)

    @property
    def servers(self) -> ServerService:
        return ServerService(self.repo, self.sync)

    @property
    def bindings(self) -> BindingService:
        return BindingService(self.repo, self.sync)

    @property
    def access(self) -> AccessService:
        return AccessService(self.repo, self.sync)

    @property
    def apikeys(self) -> ApiKeyService:
        return ApiKeyService(self.repo, self.sync)

    @property
    def auth(self) -> AuthService:
        return AuthService(self.users)

    @property
    def policies(self) -> PolicyService:
        return PolicyService(self.repo, self.sync)

    @property
    def workspace(self) -> WorkspaceService:
        s = get_settings()
        return WorkspaceService(self.repo, self.sync, s, GoogleDirectory(self.oidc.http, s))

    @property
    def sso(self) -> SsoService:
        return SsoService(self.repo, self.sync, self.pools.hot, self.oidc, get_settings())

    @property
    def outbox(self) -> Outbox:
        return Outbox(self.repo, self.sync, reconcile=lambda: self.reconciler.run_once())

    @property
    def reconciler(self) -> Reconciler:
        return Reconciler(self.repo, self.sync)

    @property
    def logs(self) -> AccessLogConsumer:
        return AccessLogConsumer(self.repo, self.pools)

    @property
    def metrics(self) -> MetricsService:
        return MetricsService(self.pools.hot)

    @property
    def stats(self) -> StatsService:
        return StatsService(self.repo, self.pools.hot)

    @property
    def artifacts(self) -> ArtifactIndex:
        return ArtifactIndex(get_settings())


def get_container(request: Request) -> Container:
    return request.app.state.container


C = Annotated[Container, Depends(get_container)]


async def current_principal(c: C, authorization: Annotated[str | None, Header()] = None) -> Principal:
    """JWT + live status check: a suspended/deleted admin (or a suspended company) loses console access
    immediately, not at token expiry. One strongly-consistent point read per request."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token")
    try:
        claims = decode_jwt(authorization.split(" ", 1)[1])
    except jwt.PyJWTError as e:
        raise Unauthorized("invalid token") from e
    try:
        user = await c.users.get(claims["cid"], claims["sub"])
    except NotFound as e:
        raise Unauthorized("user no longer exists") from e
    if user.status != Status.ACTIVE or user.role == Role.MEMBER or not (user.password_hash or user.identities):
        raise Unauthorized("account is not allowed to log in")
    if user.role != Role.MASTER and (await c.companies.get(user.company_id)).status != Status.ACTIVE:
        raise Unauthorized("company is not active")
    return Principal(user_id=user.user_id, company_id=user.company_id, role=user.role, email=user.email)


P = Annotated[Principal, Depends(current_principal)]


def require_master(p: P) -> Principal:
    if not p.is_master:
        raise Forbidden("master role required")
    return p


M = Annotated[Principal, Depends(require_master)]


def require_company_access(p: Principal, company_id: str) -> None:
    """Master: any company. Admin: own company only. Member: no API access."""
    if p.is_master:
        return
    if p.role == Role.ADMIN and p.company_id == company_id:
        return
    raise Forbidden("no access to this company")
