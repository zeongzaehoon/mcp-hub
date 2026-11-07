"""API key lifecycle. A key acts on behalf of its user (company -> user -> server): it carries no
permissions of its own, only a scope over the user's ServerAccess grants. Issuing, re-activating or
rotating a key requires company, user and every scoped access to be active — DynamoDB
ConditionChecks in the same TransactWriteItems as the Put, so a concurrent suspend cannot slip in
between a read and a write. Server/binding state is re-checked on every MCP call."""

from datetime import datetime

from app.core.errors import Conflict, InvariantViolation, NotFound
from app.core.security import generate_api_key, hash_api_key
from app.domain.enums import Status
from app.domain.models import ApiKey, now
from app.infra.dynamo.keys import access_sk, company_pk, keyhash_uniq_pk
from app.services.base import ServiceBase
from app.services.companies import assert_transition
from app.services.users import UserService


class ApiKeyService(ServiceBase):
    async def get(self, company_id: str, key_id: str) -> ApiKey:
        e = await self.repo.get(company_pk(company_id), f"APIKEY#{key_id}")
        if not isinstance(e, ApiKey):
            raise NotFound(f"api key {key_id} not found in company {company_id}")
        return e

    def _guard_ops(self, k: ApiKey) -> list[dict]:
        ops = [
            self.repo.tx.check_exists(company_pk(k.company_id), "META", status=Status.ACTIVE),
            self.repo.tx.check_exists(company_pk(k.company_id), f"USER#{k.user_id}", status=Status.ACTIVE),
        ]
        # each scoped server must be one the user holds an active access to (same company partition)
        ops += [
            self.repo.tx.check_exists(company_pk(k.company_id), access_sk(k.user_id, sid), status=Status.ACTIVE)
            for sid in k.server_ids or []
        ]
        return ops

    async def create(
        self, *, company_id: str, user_id: str, server_ids: list[str] | None, name: str,
        expires_at: datetime | None, rate_limit_per_minute: int | None = None,
    ) -> tuple[ApiKey, str]:
        # user must belong to this company — the SK check below (COMPANY#cid / USER#uid) enforces it
        user = await UserService(self.repo, self.sync).get(company_id, user_id)
        if expires_at is not None and expires_at <= now():
            raise InvariantViolation("expires_at must be in the future")
        raw, key_hash, prefix = generate_api_key()
        try:
            k = ApiKey(
                company_id=company_id, user_id=user.user_id, server_ids=server_ids,
                key_hash=key_hash, key_prefix=prefix, name=name, expires_at=expires_at,
                rate_limit_per_minute=rate_limit_per_minute,
            )
        except ValueError as e:
            raise InvariantViolation(str(e)) from e
        ops = [
            *self._guard_ops(k),
            self.repo.tx.put_raw(
                {"PK": keyhash_uniq_pk(key_hash), "SK": "UNIQ", "company_id": company_id, "key_id": k.key_id},
                must_not_exist=True,
            ),
            self.repo.tx.create(k),
        ]
        try:
            await self.commit([k], ops)
        except Conflict as e:
            raise Conflict(
                "cannot issue key: company or user is not active, or the user has no active access to a scoped server",
                code="apikey_create_conflict",
            ) from e
        return k, raw

    async def list_for_company(self, company_id: str, *, limit: int, cursor: str | None, status: Status | None, user_id: str | None, q: str | None):
        filters = {}
        if status:
            filters["status"] = status
        if user_id:
            filters["user_id"] = user_id
        contains = {"name": q} if q else None
        items, nxt = await self.repo.query(company_pk(company_id), sk_prefix="APIKEY#", limit=limit, cursor=cursor, filters=filters or None, contains=contains, consistent=True)
        return [i for i in items if isinstance(i, ApiKey)], nxt

    async def update(
        self, company_id: str, key_id: str, *, name: str | None = None, status: Status | None = None,
        server_ids: list[str] | None = None, scope_all: bool = False,
        expires_at: datetime | None = None, clear_expiry: bool = False,
        rate_limit_per_minute: int | None = None, clear_rate_limit: bool = False,
    ) -> ApiKey:
        k = await self.get(company_id, key_id)
        data = k.model_dump()
        if name is not None:
            data["name"] = name
        scope_changed = False
        if scope_all:
            scope_changed = data["server_ids"] is not None
            data["server_ids"] = None
        elif server_ids is not None:
            scope_changed = sorted(server_ids) != (data["server_ids"] or None)
            data["server_ids"] = list(server_ids)
        if clear_rate_limit:
            data["rate_limit_per_minute"] = None
        elif rate_limit_per_minute is not None:
            data["rate_limit_per_minute"] = rate_limit_per_minute
        if clear_expiry:
            data["expires_at"] = None
        elif expires_at is not None:
            if expires_at <= now():
                raise InvariantViolation("expires_at must be in the future")
            data["expires_at"] = expires_at
        if status is not None and status != k.status:
            assert_transition(k.status, status)
            data["status"] = status
        try:
            nk = ApiKey.model_validate(data)  # re-validates scope
        except ValueError as e:
            raise InvariantViolation(str(e)) from e
        expected = self.bump(nk)
        ops = [self.repo.tx.replace(nk, expected)]
        if nk.status == Status.ACTIVE and (k.status != Status.ACTIVE or scope_changed):
            ops = [*self._guard_ops(nk), *ops]  # reactivation / new scope must re-prove the parents
        try:
            await self.commit([nk], ops)
        except Conflict as e:
            raise Conflict("stale version or parents not active", code="apikey_update_conflict") from e
        return nk

    async def rotate(self, company_id: str, key_id: str) -> tuple[ApiKey, str]:
        """New secret, same identity. Old hash is tombstoned in Redis via the old entity version
        being replaced: we write a DELETED tombstone under the old hash so cached lookups deny."""
        k = await self.get(company_id, key_id)
        if k.status == Status.DELETED:
            raise InvariantViolation("cannot rotate a deleted key")
        old_hash = k.key_hash
        raw, key_hash, prefix = generate_api_key()
        nk = k.model_copy(update={"key_hash": key_hash, "key_prefix": prefix})
        expected = self.bump(nk)
        ops = [
            *self._guard_ops(nk),
            self.repo.tx.delete_raw(keyhash_uniq_pk(old_hash), "UNIQ"),
            self.repo.tx.put_raw({"PK": keyhash_uniq_pk(key_hash), "SK": "UNIQ", "company_id": company_id, "key_id": key_id}, must_not_exist=True),
            self.repo.tx.replace(nk, expected),
        ]
        try:
            await self.commit([nk], ops)
        except Conflict as e:
            raise Conflict("rotation failed: parents not active or stale version", code="apikey_rotate_conflict") from e
        # tombstone the old hash (no version race: the old key hash never comes back)
        tomb = k.model_copy(update={"status": Status.DELETED, "version": nk.version})
        await self.sync.write(tomb)
        return nk, raw

    async def resolve(self, raw_key: str) -> ApiKey | None:
        ref = await self.repo.get_raw(keyhash_uniq_pk(hash_api_key(raw_key)), "UNIQ")
        if not ref:
            return None
        return await self.get(ref["company_id"], ref["key_id"])
