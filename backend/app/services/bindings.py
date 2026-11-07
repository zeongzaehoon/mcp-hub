from app.core.errors import Conflict, InvariantViolation, NotFound
from app.core.security import encrypt_secret
from app.domain.enums import Status
from app.domain.models import TenantBinding
from app.domain.tenant import TenantCredentials
from app.infra.dynamo.keys import company_pk, server_pk
from app.services.base import ServiceBase
from app.services.companies import assert_transition
from app.services.servers import ServerService


class BindingService(ServiceBase):
    async def get(self, company_id: str, server_id: str) -> TenantBinding:
        e = await self.repo.get(company_pk(company_id), f"BINDING#{server_id}")
        if not isinstance(e, TenantBinding):
            raise NotFound(f"binding {company_id}/{server_id} not found")
        return e

    @staticmethod
    def _encrypt_credentials(tenant: dict) -> dict:
        creds = tenant.get("credentials")
        if creds and "password" in creds:
            tenant = {**tenant, "credentials": TenantCredentials(
                username=creds["username"], password_enc=encrypt_secret(creds["password"])
            ).model_dump()}
        return tenant

    async def create(self, *, company_id: str, server_id: str, tenant: dict) -> TenantBinding:
        server = await ServerService(self.repo, self.sync).get(server_id)
        if server.status != Status.ACTIVE:
            raise InvariantViolation("server is not active")
        b = TenantBinding(company_id=company_id, server_id=server_id, server_type=server.server_type, tenant=self._encrypt_credentials(tenant))
        ops = [
            self.repo.tx.check_exists(company_pk(company_id), "META", status=Status.ACTIVE),
            self.repo.tx.check_exists(server_pk(server_id), "META", status=Status.ACTIVE),
            self.repo.tx.create(b),
        ]
        try:
            await self.commit([b], ops)
        except Conflict as e:
            raise Conflict("company/server not active or binding already exists", code="binding_create_conflict") from e
        return b

    async def list_for_company(self, company_id: str, *, limit: int, cursor: str | None):
        items, nxt = await self.repo.query(company_pk(company_id), sk_prefix="BINDING#", limit=limit, cursor=cursor, consistent=True)
        return [i for i in items if isinstance(i, TenantBinding)], nxt

    async def list_for_server(self, server_id: str, *, limit: int, cursor: str | None):
        items, nxt = await self.repo.query(server_pk(server_id), index="gsi1", sk_prefix="BINDING#", limit=limit, cursor=cursor)
        return [i for i in items if isinstance(i, TenantBinding)], nxt

    async def update(self, company_id: str, server_id: str, *, tenant: dict | None = None, status: Status | None = None) -> TenantBinding:
        b = await self.get(company_id, server_id)
        if tenant is not None:
            b = b.model_copy(update={"tenant": self._encrypt_credentials(tenant)})
            b = TenantBinding.model_validate(b.model_dump())  # re-run mode validation
        if status is not None and status != b.status:
            assert_transition(b.status, status)
            b.status = status
        expected = self.bump(b)
        await self.commit([b], [self.repo.tx.replace(b, expected)])
        return b
