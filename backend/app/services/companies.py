import logging

from app.core.errors import InvariantViolation, NotFound
from app.domain.enums import SYSTEM_COMPANY_ID, Status
from app.domain.models import ApiKey, Branding, Company, IdentityProvider, ServerAccess, TenantBinding, User
from app.infra.dynamo.keys import company_pk, email_uniq_pk, identity_uniq_pk, idp_domain_uniq_pk
from app.services.base import ServiceBase

log = logging.getLogger(__name__)

# suspended -> active allowed; deleted is terminal
ALLOWED_TRANSITIONS = {
    Status.ACTIVE: {Status.SUSPENDED, Status.DELETED},
    Status.SUSPENDED: {Status.ACTIVE, Status.DELETED},
    Status.DELETED: set(),
}


def assert_transition(cur: Status, nxt: Status) -> None:
    if nxt not in ALLOWED_TRANSITIONS[cur]:
        raise InvariantViolation(f"cannot change status {cur} -> {nxt}", code="bad_transition")


class CompanyService(ServiceBase):
    async def get(self, company_id: str) -> Company:
        e = await self.repo.get(company_pk(company_id), "META")
        if not isinstance(e, Company):
            raise NotFound(f"company {company_id} not found")
        return e

    async def create(self, *, company_id: str, name: str, created_by: str) -> Company:
        if company_id == SYSTEM_COMPANY_ID:
            raise InvariantViolation("reserved company id")
        c = Company(company_id=company_id, name=name, created_by=created_by)
        await self.commit([c], [self.repo.tx.create(c)])
        return c

    async def list(self, *, limit: int, cursor: str | None, status: Status | None, q: str | None):
        filters = {"status": status} if status else None
        contains = {"name": q} if q else None
        items, nxt = await self.repo.query("TYPE#COMPANY", index="gsi1", limit=limit, cursor=cursor, filters=filters, contains=contains)
        return [i for i in items if isinstance(i, Company) and not i.is_system], nxt

    async def update(self, company_id: str, *, name: str | None = None, status: Status | None = None) -> Company:
        c = await self.get(company_id)
        if c.is_system:
            raise InvariantViolation("system company is immutable")
        if name is not None:
            c.name = name
        if status is not None and status != c.status:
            assert_transition(c.status, status)
            c.status = status
        expected = self.bump(c)
        await self.commit([c], [self.repo.tx.replace(c, expected)])
        if c.status == Status.DELETED:
            await self._cascade_delete(c)
        return c

    async def set_branding(self, company_id: str, branding: Branding | None) -> Company:
        c = await self.get(company_id)
        if c.is_system:
            raise InvariantViolation("system company has no branding")
        c.branding = branding
        expected = self.bump(c)
        await self.commit([c], [self.repo.tx.replace(c, expected)])
        return c

    async def _cascade_delete(self, c: Company) -> None:
        """Tombstone children for audit clarity. Security does not depend on this: MCP servers
        compute effective status from the company key too. Chunked; safe to re-run."""
        children, cursor = [], None
        while True:
            page, cursor = await self.repo.query(company_pk(c.company_id), limit=100, cursor=cursor, consistent=True)
            children.extend(page)
            if not cursor:
                break
        idp = next((e for e in children if isinstance(e, IdentityProvider) and e.status != Status.DELETED), None)
        if idp is not None:  # its own transaction: up to 20 domain markers
            idp.status = Status.DELETED
            await self.commit([idp], [self.repo.tx.replace(idp, self.bump(idp)), *[self.repo.tx.delete_raw(idp_domain_uniq_pk(d), "UNIQ") for d in idp.domains]])
        for chunk_start in range(0, len(children), 20):  # 20 entities + 20 outbox + email + identity frees stay < 100
            chunk = [e for e in children[chunk_start : chunk_start + 20] if isinstance(e, (User, TenantBinding, ServerAccess, ApiKey)) and e.status != Status.DELETED]
            if not chunk:
                continue
            ops = []
            for e in chunk:
                e.status = Status.DELETED
                ops.append(self.repo.tx.replace(e, self.bump(e)))
                if isinstance(e, User):
                    ops.append(self.repo.tx.delete_raw(email_uniq_pk(e.email), "UNIQ"))  # same as UserService.update
                    ops += [self.repo.tx.delete_raw(identity_uniq_pk(i.issuer, i.subject), "UNIQ") for i in e.identities]
            try:
                await self.commit(chunk, ops)
            except Exception:
                log.exception("cascade chunk failed for company %s; rerun update to complete", c.company_id)
                raise
