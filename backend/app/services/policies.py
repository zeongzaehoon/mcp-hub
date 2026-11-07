"""Team-level authorization: an AccessPolicy says what a team (or everyone in the company) may do on a server, and
`apply()` materialises it into ordinary ServerAccess rows (`source=policy`) for the users who are members. The MCP
path, the resolver and every invariant keep reading ServerAccess only — policies never bypass them.

Rules (manual wins, user decision 2026-11-07):
  * a manual ServerAccess on (user, server) is never touched by policies;
  * a policy row is the union of all active policies covering the user for that server (its own teams + everyone);
  * a user no longer covered keeps a suspended policy row (audit), reactivated when covered again;
  * suspended users / inactive bindings are skipped — the row stays as it is and the resolver denies anyway.
Triggered after a directory sync, after a policy change, and on demand (`POST /companies/{cid}/policies/apply`)."""

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.errors import Conflict, InvariantViolation, NotFound
from app.domain.enums import Permission, Status
from app.domain.models import (
    AccessPolicy,
    ServerAccess,
    Team,
    TeamMembership,
    TenantBinding,
    User,
    policy_id_for,
)
from app.infra.dynamo.keys import company_pk, policy_sk
from app.services.access import AccessService
from app.services.base import ServiceBase
from app.services.companies import assert_transition
from app.services.servers import ServerService

log = logging.getLogger(__name__)


@dataclass
class ApplyReport:
    users: int = 0
    created: int = 0
    updated: int = 0
    suspended: int = 0
    reactivated: int = 0
    kept_manual: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["errors"] = d["errors"][:50]
        return d


class PolicyService(ServiceBase):
    async def get(self, company_id: str, policy_id: str) -> AccessPolicy:
        e = await self.repo.get(company_pk(company_id), policy_sk(policy_id))
        if not isinstance(e, AccessPolicy):
            raise NotFound(f"policy {policy_id} not found in company {company_id}")
        return e

    async def list(self, company_id: str, *, limit: int, cursor: str | None, server_id: str | None = None, team_id: str | None = None):
        filters: dict[str, Any] = {}
        if server_id:
            filters["server_id"] = server_id
        if team_id:
            filters["team_id"] = team_id
        items, nxt = await self.repo.query(company_pk(company_id), sk_prefix="POLICY#", limit=limit, cursor=cursor, filters=filters or None, consistent=True)
        return [i for i in items if isinstance(i, AccessPolicy)], nxt

    async def create(self, *, company_id: str, team_id: str | None, server_id: str, permissions: set[Permission],
                     allowed_tools: set[str]) -> AccessPolicy:
        server = await ServerService(self.repo, self.sync).get(server_id)
        if team_id is not None:
            t = await self.repo.get(company_pk(company_id), f"TEAM#{team_id}")
            if not isinstance(t, Team) or t.status == Status.DELETED:
                raise NotFound(f"team {team_id} not found in company {company_id}")
        try:
            p = AccessPolicy(policy_id=policy_id_for(company_id, team_id, server_id), company_id=company_id, team_id=team_id,
                             server_id=server.server_id, server_type=server.server_type, permissions=set(permissions),
                             allowed_tools=set(allowed_tools))
        except ValueError as e:
            raise InvariantViolation(str(e)) from e
        ops = [
            self.repo.tx.check_exists(company_pk(company_id), "META", status=Status.ACTIVE),
            self.repo.tx.check_exists(company_pk(company_id), f"BINDING#{server_id}", status=Status.ACTIVE),  # the company must be bound to the server
            self.repo.tx.create(p),
        ]
        try:
            await self.commit([p], ops)
        except Conflict as e:
            raise Conflict("company not active, no active binding to this server, or a policy for this team/server already exists",
                           code="policy_create_conflict") from e
        return p

    async def update(self, company_id: str, policy_id: str, *, permissions: set[Permission] | None = None,
                     allowed_tools: set[str] | None = None, status: Status | None = None) -> AccessPolicy:
        p = await self.get(company_id, policy_id)
        data = p.model_dump()
        if permissions is not None:
            data["permissions"] = set(permissions)
        if allowed_tools is not None:
            data["allowed_tools"] = set(allowed_tools)
        if status is not None and status != p.status:
            assert_transition(p.status, status)
            data["status"] = status
        try:
            np = AccessPolicy.model_validate(data)
        except ValueError as e:
            raise InvariantViolation(str(e)) from e
        expected = self.bump(np)
        await self.commit([np], [self.repo.tx.replace(np, expected)])
        return np

    # ---- materialisation ------------------------------------------------------------------------------
    async def apply(self, company_id: str) -> ApplyReport:
        rep = ApplyReport()
        rows = await self._all(company_pk(company_id))
        policies = [p for p in rows if isinstance(p, AccessPolicy) and p.status == Status.ACTIVE]
        users = [u for u in rows if isinstance(u, User)]
        bindings = {b.server_id: b for b in rows if isinstance(b, TenantBinding) and b.status == Status.ACTIVE}
        teams_active = {t.team_id for t in rows if isinstance(t, Team) and t.status == Status.ACTIVE}
        memberships: dict[str, set[str]] = {}
        for m in rows:
            if isinstance(m, TeamMembership) and m.status == Status.ACTIVE and m.team_id in teams_active:
                memberships.setdefault(m.user_id, set()).add(m.team_id)
        access_rows = {(a.user_id, a.server_id): a for a in rows if isinstance(a, ServerAccess)}
        access = AccessService(self.repo, self.sync)

        for u in users:
            if u.status == Status.DELETED:
                continue
            rep.users += 1
            my_teams = memberships.get(u.user_id, set())
            wanted: dict[str, tuple[set[Permission], set[str], list[str]]] = {}
            for p in policies:
                if p.server_id not in bindings or (p.team_id is not None and p.team_id not in my_teams):
                    continue
                perms, tools, ids = wanted.setdefault(p.server_id, (set(), set(), []))
                perms |= p.permissions
                tools |= p.allowed_tools
                ids.append(p.policy_id)
            # rows this user holds that policies made: suspend those no longer covered
            for (uid, sid), a in access_rows.items():
                if uid != u.user_id or a.source != "policy":
                    continue
                if sid in wanted:
                    continue
                if a.status == Status.ACTIVE:
                    try:
                        await access.update(company_id, uid, sid, status=Status.SUSPENDED)
                        rep.suspended += 1
                    except Exception as e:  # noqa: BLE001
                        rep.errors.append(f"{uid}/{sid}: {e}")
            if u.status != Status.ACTIVE:
                continue  # nothing new for a suspended user; the resolver denies them regardless
            for sid, (perms, tools, ids) in wanted.items():
                a = access_rows.get((u.user_id, sid))
                if a is not None and a.source == "manual":
                    rep.kept_manual += 1
                    continue
                try:
                    if a is None:
                        na = await access.create(company_id=company_id, user_id=u.user_id, server_id=sid, permissions=perms, allowed_tools=tools)
                        na.source, na.policy_ids = "policy", sorted(ids)
                        await self.commit([na], [self.repo.tx.replace(na, self.bump(na))])
                        access_rows[(u.user_id, sid)] = na
                        rep.created += 1
                    elif a.status != Status.ACTIVE or a.permissions != perms or a.allowed_tools != tools or sorted(a.policy_ids) != sorted(ids):
                        was_suspended = a.status != Status.ACTIVE
                        na = await access.update(company_id, u.user_id, sid, status=Status.ACTIVE if was_suspended else None,
                                                 permissions=perms, allowed_tools=tools)
                        na.policy_ids = sorted(ids)
                        await self.commit([na], [self.repo.tx.replace(na, self.bump(na))])
                        access_rows[(u.user_id, sid)] = na
                        rep.reactivated += 1 if was_suspended else 0
                        rep.updated += 0 if was_suspended else 1
                except Exception as e:  # noqa: BLE001 - one user's conflict must not stop the rest
                    rep.errors.append(f"{u.user_id}/{sid}: {e}")
        return rep

    async def _all(self, pk: str) -> list:
        out, cursor = [], None
        while True:
            page, cursor = await self.repo.query(pk, limit=200, cursor=cursor, consistent=True)
            out.extend(page)
            if not cursor:
                return out
