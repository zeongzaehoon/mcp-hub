from enum import StrEnum


class Status(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"  # reversible
    DELETED = "deleted"  # terminal; tombstone kept for audit


class Role(StrEnum):
    MASTER = "master"  # global operator, belongs to SYSTEM company
    ADMIN = "admin"  # company admin, may manage users/keys of own company
    MEMBER = "member"  # key owner only


class Permission(StrEnum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    EXPORT = "export"  # produce files (CSV/XLSX) from query results — separate from READ on purpose
    EXECUTE = "execute"  # run an active security tool against a target — for the ethie pentest server


class ServerType(StrEnum):
    POSTGRES = "postgres"
    CLICKHOUSE = "clickhouse"
    MONGO = "mongo"
    HACKER = "hacker"


class EntityType(StrEnum):
    COMPANY = "COMPANY"
    USER = "USER"
    SERVER = "SERVER"
    BINDING = "BINDING"
    ACCESS = "ACCESS"
    APIKEY = "APIKEY"
    IDP = "IDP"  # a company's external identity provider (OIDC)
    WORKSPACE = "WORKSPACE"  # a company's Google Workspace directory connection (service-account key, sync state)
    TEAM = "TEAM"  # a group of users (synced from the directory, or manual)
    TEAM_MEMBER = "TEAM_MEMBER"  # user <-> team edge
    POLICY = "POLICY"  # team (or everyone) -> server grant template, materialised into ServerAccess rows


SYSTEM_COMPANY_ID = "system"
