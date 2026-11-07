"""Google Workspace directory sync: the company's users and groups become Users (members) and Teams here, so an
operator never types employees in by hand. The directory is read as a service account with domain-wide delegation
whose key an operator registers per company (decision 2026-11-07: the console has no SSO login; connecting a company's
Workspace is an operator action, not a Workspace admin's Google login). What a team may do is phase 3 (access policies).

Coexistence with the manual path (user decision 2026-11-07): records carry `source`. The sync creates and updates
only `workspace` records; a `manual` user matched by email merely gains its directory id / identity link. Status
propagates one way — a user suspended or missing in the directory is suspended here (`suspended_by_sync`) and only
such users are reactivated by the sync; an operator's suspension is never undone. Memberships the sync made and that
vanished from the group are removed; manual memberships stay.

Runs: after the key is registered (best effort), on demand (`POST /companies/{cid}/workspace/sync`) and on a schedule
(`WORKSPACE_SYNC_INTERVAL_S`). One run per company at a time (in-process lock)."""

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar

from app.core.config import Settings
from app.core.errors import Conflict, InvariantViolation, NotFound
from app.core.google_directory import DIRECTORY_SCOPES, DirectoryError, GoogleDirectory, ServiceAccountKey
from app.core.security import decrypt_secret, encrypt_secret
from app.domain.enums import Role, Status
from app.domain.models import (
    Identity,
    Team,
    TeamMembership,
    User,
    WorkspaceConnection,
    now,
    workspace_team_id,
)
from app.infra.dynamo.keys import WORKSPACE_SK, company_pk, team_member_sk, team_sk, workspace_domain_uniq_pk
from app.services.base import ServiceBase
from app.services.policies import PolicyService
from app.services.users import UserService

log = logging.getLogger(__name__)


@dataclass
class SyncReport:
    users_seen: int = 0
    users_created: int = 0
    users_updated: int = 0
    users_suspended: int = 0
    users_reactivated: int = 0
    teams_seen: int = 0
    teams_created: int = 0
    teams_updated: int = 0
    teams_suspended: int = 0
    members_added: int = 0
    members_removed: int = 0
    skipped: list[str] = field(default_factory=list)  # emails/groups the sync could not place (max 50 kept)
    policies: dict[str, Any] | None = None  # ApplyReport of the policy materialisation that follows

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["skipped"] = d["skipped"][:50]
        return d


class WorkspaceService(ServiceBase):
    _locks: ClassVar[dict[str, asyncio.Lock]] = {}  # per company, process-wide: one sync at a time

    def __init__(self, repo, sync, settings: Settings, directory: GoogleDirectory):
        super().__init__(repo, sync)
        self.s = settings
        self.directory = directory
        self.users = UserService(repo, sync)

    # ---- connection ---------------------------------------------------------------------------------
    async def get(self, company_id: str) -> WorkspaceConnection:
        e = await self.repo.get(company_pk(company_id), WORKSPACE_SK)
        if not isinstance(e, WorkspaceConnection) or e.status == Status.DELETED:
            raise NotFound(f"company {company_id} has no workspace connection")
        return e

    async def connect(self, company_id: str, *, domain: str, admin_email: str, service_account_json: str | None,
                      connected_by: str) -> WorkspaceConnection:
        """Register (or replace) the company's service-account key and the admin it impersonates. Omitting the key on
        an existing live connection keeps the stored one (e.g. only the admin changes). The domain is unique across
        companies; a sync is kicked afterwards by the caller."""
        domain = domain.strip().lower()
        admin_email = admin_email.strip().lower()
        if not domain or "." not in domain or "@" in domain or "/" in domain:
            raise InvariantViolation("domain must be a bare DNS name such as example.com")
        if not admin_email.endswith("@" + domain):
            raise InvariantViolation("admin_email must belong to the domain", code="workspace_admin_domain")
        cur = await self.repo.get(company_pk(company_id), WORKSPACE_SK)
        cur = cur if isinstance(cur, WorkspaceConnection) else None
        live = cur if cur is not None and cur.status != Status.DELETED else None
        if service_account_json is not None:
            try:
                key = ServiceAccountKey.parse(service_account_json)
            except ValueError as e:
                raise InvariantViolation(f"service account key rejected: {e}", code="workspace_bad_key") from e
            enc, client_email, client_id = encrypt_secret(key.dumps()), key.client_email, key.client_id
        elif live is not None and live.service_account_enc:
            enc, client_email, client_id = live.service_account_enc, live.client_email, live.client_id
        else:
            raise InvariantViolation("service_account_json is required to connect", code="workspace_key_required")
        conn = WorkspaceConnection(company_id=company_id, domain=domain, admin_email=admin_email, client_email=client_email,
                                   client_id=client_id, connected_by=connected_by, service_account_enc=enc,
                                   scopes=list(DIRECTORY_SCOPES), needs_reconnect=False,
                                   last_sync_at=live.last_sync_at if live else None, last_sync=live.last_sync if live else None)
        ops = [self.repo.tx.check_exists(company_pk(company_id), "META", status=Status.ACTIVE)]
        old_domain = live.domain if live else None
        if old_domain != domain:
            if old_domain:
                ops.append(self.repo.tx.delete_raw(workspace_domain_uniq_pk(old_domain), "UNIQ"))
            ops.append(self.repo.tx.put_raw({"PK": workspace_domain_uniq_pk(domain), "SK": "UNIQ", "company_id": company_id}, must_not_exist=True))
        if cur is not None:  # a tombstone keeps its row: replace it
            conn.created_at, conn.version = cur.created_at, cur.version
            ops.append(self.repo.tx.replace(conn, self.bump(conn)))
        else:
            ops.append(self.repo.tx.create(conn))
        try:
            await self.commit([conn], ops)
        except Conflict as e:
            raise Conflict("company not active, stale connection, or the domain is connected by another company",
                           code="workspace_domain_taken") from e
        return conn

    async def disconnect(self, company_id: str) -> None:
        conn = await self.get(company_id)
        conn.status, conn.service_account_enc = Status.DELETED, None  # the key is wiped, synced records stay
        ops = [self.repo.tx.replace(conn, self.bump(conn)), self.repo.tx.delete_raw(workspace_domain_uniq_pk(conn.domain), "UNIQ")]
        await self.commit([conn], ops)

    # ---- teams ------------------------------------------------------------------------------------
    async def list_teams(self, company_id: str, *, limit: int, cursor: str | None):
        items, nxt = await self.repo.query(company_pk(company_id), sk_prefix="TEAM#", limit=limit, cursor=cursor, consistent=True)
        return [i for i in items if isinstance(i, Team)], nxt

    async def get_team(self, company_id: str, team_id: str) -> Team:
        e = await self.repo.get(company_pk(company_id), team_sk(team_id))
        if not isinstance(e, Team):
            raise NotFound(f"team {team_id} not found in company {company_id}")
        return e

    async def list_members(self, company_id: str, team_id: str) -> list[TeamMembership]:
        await self.get_team(company_id, team_id)
        return [m for m in await self._all(company_pk(company_id), f"TEAMMEMBER#{team_id}#") if isinstance(m, TeamMembership)]

    async def teams_of_user(self, company_id: str, user_id: str) -> list[str]:
        """team ids the user belongs to (active memberships) — what phase 3 resolves policies from."""
        items, _ = await self.repo.query(f"USER#{user_id}", index="gsi2", sk_prefix="TEAMMEMBER#", limit=1000, cursor=None)
        return sorted(m.team_id for m in items if isinstance(m, TeamMembership) and m.company_id == company_id and m.status == Status.ACTIVE)

    async def _all(self, pk: str, sk_prefix: str) -> list:
        out, cursor = [], None
        while True:
            page, cursor = await self.repo.query(pk, sk_prefix=sk_prefix, limit=200, cursor=cursor, consistent=True)
            out.extend(page)
            if not cursor:
                return out

    # ---- sync -------------------------------------------------------------------------------------
    async def run_sync(self, company_id: str) -> SyncReport:  # not `sync`: ServiceBase.sync is the Redis projection
        lock = self._locks.setdefault(company_id, asyncio.Lock())
        if lock.locked():
            raise Conflict("a sync for this company is already running", code="workspace_sync_running")
        async with lock:
            conn = await self.get(company_id)
            if not conn.service_account_enc:
                raise InvariantViolation("workspace has no service account key: register one", code="workspace_reconnect")
            try:
                report = await self._run(conn)
            except DirectoryError as e:
                log.warning("workspace sync %s: %s", company_id, e)
                await self._record(conn, None, error=str(e), reconnect=e.reconnect)
                raise Conflict(f"directory sync failed: {e}", code="workspace_reconnect" if e.reconnect else "workspace_sync_failed") from e
            await self._record(conn, report)
            try:  # memberships changed: re-materialise team policies into ServerAccess rows
                applied = await PolicyService(self.repo, self.sync).apply(conn.company_id)
                report.policies = applied.as_dict()
            except Exception as e:  # noqa: BLE001 - the sync itself succeeded; policy errors are reported, not fatal
                log.warning("policy apply after sync %s: %s", conn.company_id, e)
                report.policies = {"error": str(e)}
            return report

    async def _record(self, conn: WorkspaceConnection, report: SyncReport | None, *, error: str | None = None, reconnect: bool = False) -> None:
        fresh = await self.get(conn.company_id)  # the run may have taken a while: never clobber a newer connection
        if report is not None:
            fresh.last_sync_at, fresh.last_sync, fresh.sync_error, fresh.needs_reconnect = now(), report.as_dict(), None, False
        else:
            fresh.sync_error, fresh.needs_reconnect = error, reconnect or fresh.needs_reconnect
        await self.commit([fresh], [self.repo.tx.replace(fresh, self.bump(fresh))])

    async def _run(self, conn: WorkspaceConnection) -> SyncReport:
        rep = SyncReport()
        key = ServiceAccountKey.loads(decrypt_secret(conn.service_account_enc or ""))
        token = await self.directory.access_token(key, conn.admin_email)
        issuer = self.s.google_issuer.rstrip("/")  # Google user ids are the OIDC `sub` of the connector SSO login
        cid = conn.company_id

        # -- users
        ws_users: dict[str, dict[str, Any]] = {}
        for u in await self.directory.list_users(token, conn.domain):
            email = str(u.get("primaryEmail") or "").lower()
            if email.rsplit("@", 1)[-1] == conn.domain and u.get("id"):
                ws_users[email] = u
        rep.users_seen = len(ws_users)
        local = [u for u in await self._all(company_pk(cid), "USER#") if isinstance(u, User)]
        by_email = {u.email: u for u in local}
        by_ext = {u.external_id: u for u in local if u.external_id}
        seen_ids: set[str] = set()
        for email, wu in ws_users.items():
            ext = str(wu["id"])
            seen_ids.add(ext)
            ws_suspended = bool(wu.get("suspended") or wu.get("archived"))
            name = str((wu.get("name") or {}).get("fullName") or email.split("@", 1)[0])[:120] or email
            u = by_ext.get(ext) or by_email.get(email)
            if u is None:
                if ws_suspended:
                    continue  # never create someone who could not log in anyway
                try:
                    await self.users.create(company_id=cid, email=email, name=name, role=Role.MEMBER, password=None,
                                            identities=[Identity(issuer=issuer, subject=ext)], source="workspace", external_id=ext)
                    rep.users_created += 1
                except Conflict:
                    rep.skipped.append(email)  # email or identity owned elsewhere: an operator decides
                continue
            if u.status == Status.DELETED:
                continue
            changed = False
            if u.external_id is None:
                u.external_id, changed = ext, True
            if not any(i.issuer == issuer for i in u.identities):
                try:
                    u = await self.users.link_identity(u, Identity(issuer=issuer, subject=ext))
                except Conflict:
                    rep.skipped.append(email)
                    continue
            if u.source == "workspace" and u.name != name:
                u.name, changed = name, True
            if ws_suspended and u.status == Status.ACTIVE:
                u.status, u.suspended_by_sync, changed = Status.SUSPENDED, True, True
                rep.users_suspended += 1
            elif not ws_suspended and u.status == Status.SUSPENDED and u.suspended_by_sync:
                u.status, u.suspended_by_sync, changed = Status.ACTIVE, False, True
                rep.users_reactivated += 1
            if changed:
                await self._replace(u)
                rep.users_updated += 1
        for u in local:  # synced users that left the directory
            if u.source == "workspace" and u.external_id and u.external_id not in seen_ids and u.status == Status.ACTIVE:
                u.status, u.suspended_by_sync = Status.SUSPENDED, True
                await self._replace(u)
                rep.users_suspended += 1
        local = [u for u in await self._all(company_pk(cid), "USER#") if isinstance(u, User)]
        by_email = {u.email: u for u in local}
        by_ext = {u.external_id: u for u in local if u.external_id}

        # -- groups -> teams
        groups: dict[str, dict[str, Any]] = {}
        for g in await self.directory.list_groups(token, conn.domain):
            if g.get("id") and g.get("email"):
                groups[str(g["id"])] = g
        rep.teams_seen = len(groups)
        teams = {t.team_id: t for t in await self._all(company_pk(cid), "TEAM#") if isinstance(t, Team)}
        for ext, g in groups.items():
            tid = workspace_team_id(cid, ext)
            name = str(g.get("name") or g["email"])[:120]
            email = str(g["email"]).lower()
            t = teams.get(tid)
            if t is None:
                t = Team(team_id=tid, company_id=cid, name=name, email=email, external_id=ext, source="workspace")
                await self.commit([t], [self.repo.tx.create(t)])
                teams[tid] = t
                rep.teams_created += 1
            elif t.name != name or t.email != email or t.status != Status.ACTIVE:
                t.name, t.email, t.status = name, email, Status.ACTIVE
                await self._replace(t)
                rep.teams_updated += 1
            # memberships
            wanted: set[str] = set()
            for m in await self.directory.list_members(token, ext):
                if str(m.get("type") or "USER").upper() != "USER":
                    continue
                mu = by_ext.get(str(m.get("id") or "")) or by_email.get(str(m.get("email") or "").lower())
                if mu is not None and mu.status != Status.DELETED:
                    wanted.add(mu.user_id)
            have = {m.user_id: m for m in await self.list_members(cid, tid)}
            for uid in wanted - set(have):
                mm = TeamMembership(company_id=cid, team_id=tid, user_id=uid, source="workspace")
                try:
                    await self.commit([mm], [self.repo.tx.create(mm)])
                    rep.members_added += 1
                except Conflict:
                    pass  # concurrent run added it
            for uid, mm in have.items():
                if uid not in wanted and mm.source == "workspace":
                    await self.repo.delete_raw(company_pk(cid), team_member_sk(tid, uid))
                    rep.members_removed += 1
        for t in teams.values():  # synced teams whose group is gone
            if t.source == "workspace" and t.external_id not in groups and t.status == Status.ACTIVE:
                t.status = Status.SUSPENDED
                await self._replace(t)
                rep.teams_suspended += 1
        return rep

    async def _replace(self, e) -> None:
        await self.commit([e], [self.repo.tx.replace(e, self.bump(e))])

    # ---- scheduler ----------------------------------------------------------------------------------
    async def sync_all(self) -> dict[str, str]:
        out: dict[str, str] = {}
        async for e in self.repo.scan_entities(entity_type="WORKSPACE"):
            if not isinstance(e, WorkspaceConnection) or e.status != Status.ACTIVE or e.needs_reconnect:
                continue
            try:
                r = await self.run_sync(e.company_id)
                out[e.company_id] = f"ok users={r.users_seen} teams={r.teams_seen}"
            except Exception as ex:  # noqa: BLE001 - one company's failure must not stop the others
                out[e.company_id] = f"error: {ex}"
        return out

    async def run_forever(self, interval: float) -> None:
        while True:
            try:
                res = await self.sync_all()
                if res:
                    log.info("workspace sync: %s", res)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("workspace sync loop error")
            await asyncio.sleep(interval)

    async def sync_soon(self, company_id: str) -> None:
        """Best-effort background sync right after the key is registered (errors are recorded on the connection)."""
        try:
            await self.run_sync(company_id)
        except Exception as e:  # noqa: BLE001
            log.warning("post-onboarding sync for %s: %s", company_id, e)
