"""User -> server access grants (company -> user -> server hierarchy). A grant can only exist or be
re-activated while company, user, server and the company's binding to that server are active; those
are ConditionChecks in the same transaction as the write."""

from app.core.errors import Conflict, InvariantViolation, NotFound
from app.domain.enums import Permission, Status
from app.domain.models import ServerAccess
from app.infra.dynamo.keys import access_sk, company_pk, server_pk
from app.services.base import ServiceBase
from app.services.companies import assert_transition
from app.services.servers import ServerService
from app.services.users import UserService


class AccessService(ServiceBase):
    async def get(self, company_id: str, user_id: str, server_id: str) -> ServerAccess:
        e = await self.repo.get(company_pk(company_id), access_sk(user_id, server_id))
        if not isinstance(e, ServerAccess):
            raise NotFound(f"access {user_id}/{server_id} not found in company {company_id}")
        return e

    def guard_ops(self, a: ServerAccess) -> list[dict]:
        return [
            self.repo.tx.check_exists(company_pk(a.company_id), "META", status=Status.ACTIVE),
            self.repo.tx.check_exists(company_pk(a.company_id), f"USER#{a.user_id}", status=Status.ACTIVE),
            self.repo.tx.check_exists(server_pk(a.server_id), "META", status=Status.ACTIVE, extra={"server_type": a.server_type}),
            self.repo.tx.check_exists(company_pk(a.company_id), f"BINDING#{a.server_id}", status=Status.ACTIVE),
        ]

    async def create(self, *, company_id: str, user_id: str, server_id: str, permissions: set[Permission], allowed_tools: set[str]) -> ServerAccess:
        user = await UserService(self.repo, self.sync).get(company_id, user_id)  # user must belong to this company
        server = await ServerService(self.repo, self.sync).get(server_id)
        try:
            a = ServerAccess(
                company_id=company_id, user_id=user.user_id, server_id=server.server_id, server_type=server.server_type,
                permissions=set(permissions), allowed_tools=set(allowed_tools),
            )
        except ValueError as e:
            raise InvariantViolation(str(e)) from e
        try:
            await self.commit([a], [*self.guard_ops(a), self.repo.tx.create(a)])
        except Conflict as e:
            raise Conflict(
                "cannot grant access: company, user, server or binding is not active, or the access already exists",
                code="access_create_conflict",
            ) from e
        return a

    async def update(
        self, company_id: str, user_id: str, server_id: str, *, status: Status | None = None,
        permissions: set[Permission] | None = None, allowed_tools: set[str] | None = None, own: bool = False,
    ) -> ServerAccess:
        """`own=True` (an operator editing through the console/API): a policy-made row becomes manual, so policies stop
        rewriting what the operator decided. Policy materialisation passes own=False."""
        a = await self.get(company_id, user_id, server_id)
        data = a.model_dump()
        if own and a.source == "policy":
            data["source"], data["policy_ids"] = "manual", []
        if permissions is not None:
            data["permissions"] = set(permissions)
        if allowed_tools is not None:
            data["allowed_tools"] = set(allowed_tools)
        if status is not None and status != a.status:
            assert_transition(a.status, status)
            data["status"] = status
        try:
            na = ServerAccess.model_validate(data)
        except ValueError as e:
            raise InvariantViolation(str(e)) from e
        expected = self.bump(na)
        ops = [self.repo.tx.replace(na, expected)]
        if na.status == Status.ACTIVE and a.status != Status.ACTIVE:
            ops = [*self.guard_ops(na), *ops]  # reactivation re-proves the parents
        try:
            await self.commit([na], ops)
        except Conflict as e:
            raise Conflict("stale version or parents not active", code="access_update_conflict") from e
        return na

    async def list_for_user(self, company_id: str, user_id: str, *, limit: int, cursor: str | None):
        items, nxt = await self.repo.query(company_pk(company_id), sk_prefix=f"ACCESS#{user_id}#", limit=limit, cursor=cursor, consistent=True)
        return [i for i in items if isinstance(i, ServerAccess)], nxt

    async def list_for_company(self, company_id: str, *, limit: int, cursor: str | None, server_id: str | None = None):
        filters = {"server_id": server_id} if server_id else None
        items, nxt = await self.repo.query(company_pk(company_id), sk_prefix="ACCESS#", limit=limit, cursor=cursor, filters=filters, consistent=True)
        return [i for i in items if isinstance(i, ServerAccess)], nxt

    async def list_for_server(self, server_id: str, *, limit: int, cursor: str | None):
        items, nxt = await self.repo.query(server_pk(server_id), index="gsi1", sk_prefix="ACCESS#", limit=limit, cursor=cursor)
        return [i for i in items if isinstance(i, ServerAccess)], nxt
