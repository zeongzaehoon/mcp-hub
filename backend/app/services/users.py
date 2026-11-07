from app.core.errors import Conflict, InvariantViolation, NotFound
from app.core.security import hash_password
from app.domain.enums import SYSTEM_COMPANY_ID, Role, Status
from app.domain.models import Identity, User
from app.infra.dynamo.keys import company_pk, email_uniq_pk, identity_uniq_pk
from app.services.base import ServiceBase
from app.services.companies import assert_transition


class UserService(ServiceBase):
    async def get(self, company_id: str, user_id: str) -> User:
        e = await self.repo.get(company_pk(company_id), f"USER#{user_id}")
        if not isinstance(e, User):
            raise NotFound(f"user {user_id} not found in company {company_id}")
        return e

    async def get_by_email(self, email: str) -> User | None:
        ref = await self.repo.get_raw(email_uniq_pk(email), "UNIQ")
        if not ref:
            return None
        e = await self.repo.get(company_pk(ref["company_id"]), f"USER#{ref['user_id']}")
        return e if isinstance(e, User) else None

    async def get_by_identity(self, ident: Identity) -> User | None:
        ref = await self.repo.get_raw(identity_uniq_pk(ident.issuer, ident.subject), "UNIQ")
        if not ref:
            return None
        e = await self.repo.get(company_pk(ref["company_id"]), f"USER#{ref['user_id']}")
        return e if isinstance(e, User) else None

    def _identity_marker(self, u: User, ident: Identity) -> dict:
        return self.repo.tx.put_raw(
            {"PK": identity_uniq_pk(ident.issuer, ident.subject), "SK": "UNIQ", "company_id": u.company_id, "user_id": u.user_id},
            must_not_exist=True,
        )

    async def create(self, *, company_id: str, email: str, name: str, role: Role, password: str | None,
                     identities: list[Identity] | None = None, source: str = "manual", external_id: str | None = None) -> User:
        if role == Role.MASTER and company_id != SYSTEM_COMPANY_ID:
            raise InvariantViolation("master role only in system company")
        if role == Role.MASTER and not password:
            raise InvariantViolation("master users require a password")
        if role == Role.ADMIN and not password and not identities:
            raise InvariantViolation("admins require a password or an SSO identity")
        if role == Role.MEMBER and password:
            raise InvariantViolation("members cannot log in; do not set a password")
        u = User(
            company_id=company_id, email=email, name=name, role=role,
            password_hash=hash_password(password) if password else None, identities=list(identities or []),
            source=source, external_id=external_id,
        )
        ops = [
            # parent must exist and be active — enforced by the DB, not by a prior read
            self.repo.tx.check_exists(company_pk(company_id), "META", status=Status.ACTIVE),
            self.repo.tx.put_raw(
                {"PK": email_uniq_pk(u.email), "SK": "UNIQ", "company_id": company_id, "user_id": u.user_id},
                must_not_exist=True,
            ),
            *[self._identity_marker(u, i) for i in u.identities],
            self.repo.tx.create(u),
        ]
        try:
            await self.commit([u], ops)
        except Conflict as e:
            raise Conflict("company not active, or email / identity already registered", code="user_create_conflict") from e
        return u

    async def link_identity(self, u: User, ident: Identity) -> User:
        """Attach an external login to an existing user (one per issuer). The identity marker is unique across all
        companies, so a subject can never resolve to two users."""
        if any(i.issuer == ident.issuer for i in u.identities):
            raise Conflict("user already has an identity at this issuer", code="identity_conflict")
        u.identities = [*u.identities, ident]
        expected = self.bump(u)
        try:
            await self.commit([u], [self._identity_marker(u, ident), self.repo.tx.replace(u, expected)])
        except Conflict as e:
            raise Conflict("identity already linked to another user, or stale version", code="identity_conflict") from e
        return u

    async def list(self, company_id: str, *, limit: int, cursor: str | None, status: Status | None, q: str | None):
        filters = {"status": status} if status else None
        contains = {"name": q} if q else None
        items, nxt = await self.repo.query(company_pk(company_id), sk_prefix="USER#", limit=limit, cursor=cursor, filters=filters, contains=contains, consistent=True)
        return [i for i in items if isinstance(i, User)], nxt

    async def update(self, company_id: str, user_id: str, *, name: str | None = None, status: Status | None = None, password: str | None = None) -> User:
        u = await self.get(company_id, user_id)
        if name is not None:
            u.name = name
        if status is not None and status != u.status:
            assert_transition(u.status, status)
            u.status = status
        if password is not None:
            if u.role == Role.MEMBER:
                raise InvariantViolation("members cannot have passwords")
            u.password_hash = hash_password(password)
        expected = self.bump(u)
        ops = [self.repo.tx.replace(u, expected)]
        if u.status == Status.DELETED:
            ops.append(self.repo.tx.delete_raw(email_uniq_pk(u.email), "UNIQ"))  # free the email
            ops += [self.repo.tx.delete_raw(identity_uniq_pk(i.issuer, i.subject), "UNIQ") for i in u.identities]
        await self.commit([u], ops)
        return u
