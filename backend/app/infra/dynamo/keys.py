"""Single-table key layout. Every access pattern is listed here so nobody invents ad-hoc keys.

PK                      SK                 gsi1pk            gsi1sk              gsi2pk      gsi2sk
COMPANY#{cid}           META               TYPE#COMPANY      {created_at}
COMPANY#{cid}           USER#{uid}         TYPE#USER         {created_at}
SERVER#{sid}            META               TYPE#SERVER       {created_at}
COMPANY#{cid}           BINDING#{sid}      SERVER#{sid}      BINDING#{cid}
COMPANY#{cid}           ACCESS#{uid}#{sid} SERVER#{sid}      ACCESS#{cid}#{uid}  USER#{uid}  ACCESS#{sid}
COMPANY#{cid}           APIKEY#{kid}                                             USER#{uid}  APIKEY#{kid}
COMPANY#{cid}           IDP                                                          (one OIDC provider per company)
COMPANY#{cid}           WORKSPACE          TYPE#WORKSPACE    {created_at}            (directory connection, one per company)
COMPANY#{cid}           TEAM#{tid}
COMPANY#{cid}           TEAMMEMBER#{tid}#{uid}                                   USER#{uid}  TEAMMEMBER#{tid}
COMPANY#{cid}           POLICY#{pid}       SERVER#{sid}      POLICY#{cid}#{pid}     (team|everyone -> server grant template)
UNIQ#EMAIL#{email}      UNIQ                                                         -> {company_id,user_id}
UNIQ#KEYHASH#{hash}     UNIQ                                                         -> {company_id,key_id}
UNIQ#IDPDOMAIN#{domain} UNIQ                                                         -> {company_id}   (email domain -> provider)
UNIQ#IDENTITY#{sha256}  UNIQ                                                         -> {company_id,user_id}  (issuer+subject -> user)
OUTBOX                  {ts}#{id}
"""

from typing import Any

from app.domain.enums import EntityType
from app.domain.models import (
    AccessPolicy,
    ApiKey,
    Company,
    IdentityProvider,
    McpServer,
    ServerAccess,
    Team,
    TeamMembership,
    TenantBinding,
    User,
    WorkspaceConnection,
)

Entity = (Company | User | McpServer | TenantBinding | ServerAccess | ApiKey | IdentityProvider | WorkspaceConnection | Team
          | TeamMembership | AccessPolicy)

KEY_ATTRS = {"PK", "SK", "gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk", "entity_type"}


def company_pk(cid: str) -> str:
    return f"COMPANY#{cid}"


def server_pk(sid: str) -> str:
    return f"SERVER#{sid}"


def email_uniq_pk(email: str) -> str:
    return f"UNIQ#EMAIL#{email.lower()}"


def access_sk(uid: str, sid: str) -> str:
    return f"ACCESS#{uid}#{sid}"


def keyhash_uniq_pk(key_hash: str) -> str:
    return f"UNIQ#KEYHASH#{key_hash}"


IDP_SK = "IDP"
WORKSPACE_SK = "WORKSPACE"


def team_sk(tid: str) -> str:
    return f"TEAM#{tid}"


def team_member_sk(tid: str, uid: str) -> str:
    return f"TEAMMEMBER#{tid}#{uid}"


def policy_sk(pid: str) -> str:
    return f"POLICY#{pid}"


def idp_domain_uniq_pk(domain: str) -> str:
    return f"UNIQ#IDPDOMAIN#{domain.lower()}"


def workspace_domain_uniq_pk(domain: str) -> str:
    return f"UNIQ#WSDOMAIN#{domain.lower()}"


def identity_uniq_pk(issuer: str, subject: str) -> str:
    import hashlib

    return "UNIQ#IDENTITY#" + hashlib.sha256(f"{issuer}\n{subject}".encode()).hexdigest()


def entity_keys(e: Entity) -> dict[str, str]:
    if isinstance(e, Company):
        return {
            "PK": company_pk(e.company_id),
            "SK": "META",
            "gsi1pk": "TYPE#COMPANY",
            "gsi1sk": e.created_at.isoformat(),
            "entity_type": EntityType.COMPANY,
        }
    if isinstance(e, User):
        return {
            "PK": company_pk(e.company_id),
            "SK": f"USER#{e.user_id}",
            "gsi1pk": "TYPE#USER",
            "gsi1sk": e.created_at.isoformat(),
            "entity_type": EntityType.USER,
        }
    if isinstance(e, McpServer):
        return {
            "PK": server_pk(e.server_id),
            "SK": "META",
            "gsi1pk": "TYPE#SERVER",
            "gsi1sk": e.created_at.isoformat(),
            "entity_type": EntityType.SERVER,
        }
    if isinstance(e, TenantBinding):
        return {
            "PK": company_pk(e.company_id),
            "SK": f"BINDING#{e.server_id}",
            "gsi1pk": server_pk(e.server_id),
            "gsi1sk": f"BINDING#{e.company_id}",
            "entity_type": EntityType.BINDING,
        }
    if isinstance(e, ServerAccess):
        return {
            "PK": company_pk(e.company_id),
            "SK": access_sk(e.user_id, e.server_id),
            "gsi1pk": server_pk(e.server_id),
            "gsi1sk": f"ACCESS#{e.company_id}#{e.user_id}",
            "gsi2pk": f"USER#{e.user_id}",
            "gsi2sk": f"ACCESS#{e.server_id}",
            "entity_type": EntityType.ACCESS,
        }
    if isinstance(e, ApiKey):
        return {
            "PK": company_pk(e.company_id),
            "SK": f"APIKEY#{e.key_id}",
            "gsi2pk": f"USER#{e.user_id}",
            "gsi2sk": f"APIKEY#{e.key_id}",
            "entity_type": EntityType.APIKEY,
        }
    if isinstance(e, IdentityProvider):
        return {"PK": company_pk(e.company_id), "SK": IDP_SK, "entity_type": EntityType.IDP}
    if isinstance(e, WorkspaceConnection):
        return {"PK": company_pk(e.company_id), "SK": WORKSPACE_SK, "gsi1pk": "TYPE#WORKSPACE", "gsi1sk": e.created_at.isoformat(),
                "entity_type": EntityType.WORKSPACE}
    if isinstance(e, Team):
        return {"PK": company_pk(e.company_id), "SK": team_sk(e.team_id), "entity_type": EntityType.TEAM}
    if isinstance(e, TeamMembership):
        return {"PK": company_pk(e.company_id), "SK": team_member_sk(e.team_id, e.user_id),
                "gsi2pk": f"USER#{e.user_id}", "gsi2sk": f"TEAMMEMBER#{e.team_id}", "entity_type": EntityType.TEAM_MEMBER}
    if isinstance(e, AccessPolicy):
        return {"PK": company_pk(e.company_id), "SK": policy_sk(e.policy_id), "gsi1pk": server_pk(e.server_id),
                "gsi1sk": f"POLICY#{e.company_id}#{e.policy_id}", "entity_type": EntityType.POLICY}
    raise TypeError(type(e))


MODEL_BY_TYPE: dict[str, type[Entity]] = {
    EntityType.COMPANY: Company,
    EntityType.USER: User,
    EntityType.SERVER: McpServer,
    EntityType.BINDING: TenantBinding,
    EntityType.ACCESS: ServerAccess,
    EntityType.APIKEY: ApiKey,
    EntityType.IDP: IdentityProvider,
    EntityType.WORKSPACE: WorkspaceConnection,
    EntityType.TEAM: Team,
    EntityType.TEAM_MEMBER: TeamMembership,
    EntityType.POLICY: AccessPolicy,
}


def to_item(e: Entity) -> dict[str, Any]:
    data = e.model_dump(mode="json")
    if isinstance(e, ApiKey):
        # None values are dropped on write, so "all servers" must be stored explicitly
        data["scope_all"] = e.server_ids is None
    return {**data, **entity_keys(e)}


def is_legacy_apikey(item: dict[str, Any]) -> bool:
    """Pre-hierarchy key: one server_id + its own permissions, no scope fields (see services/migrate.py)."""
    return item.get("entity_type") == EntityType.APIKEY and "scope_all" not in item and "server_id" in item


def from_item(item: dict[str, Any]) -> Entity:
    model = MODEL_BY_TYPE[item["entity_type"]]
    data = {k: v for k, v in item.items() if k not in KEY_ATTRS}
    if item["entity_type"] == EntityType.APIKEY:
        if is_legacy_apikey(item):
            # read as scoped to exactly its server — a missing scope must never mean "all servers"
            data["server_ids"] = [item["server_id"]]
        elif item.get("scope_all") is True:
            data["server_ids"] = None
        data.pop("scope_all", None)
    return model.model_validate(data)
