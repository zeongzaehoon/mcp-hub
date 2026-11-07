export type Status = "active" | "suspended" | "deleted";
export const STATUS_VALUES: readonly Status[] = ["active", "suspended", "deleted"];
export const STATUS_LABEL: Record<Status, string> = {
  active: "활성",
  suspended: "일시중지",
  deleted: "삭제됨",
};

export type Role = "master" | "admin" | "member";
export const ROLE_LABEL: Record<Role, string> = {
  master: "마스터",
  admin: "관리자",
  member: "멤버",
};

export type Permission = "read" | "create" | "update" | "delete" | "export" | "execute";
export const PERMISSION_VALUES: readonly Permission[] = ["read", "create", "update", "delete", "export", "execute"];
export const PERMISSION_LABEL: Record<Permission, string> = {
  read: "읽기",
  create: "생성",
  update: "수정",
  delete: "삭제",
  export: "내보내기",
  execute: "실행",
};

export type ServerType = "postgres" | "clickhouse" | "mongo" | "hacker" | "ethie";
export const SERVER_TYPE_VALUES: readonly ServerType[] = ["postgres", "clickhouse", "mongo", "hacker", "ethie"];

export type TenantMode = "schema" | "database" | "column";
export const TENANT_MODE_LABEL: Record<TenantMode, string> = {
  schema: "스키마 분리",
  database: "데이터베이스 분리",
  column: "컬럼 값 분리",
};

/** Mirrors backend `app.domain.tenant.ALLOWED_MODES`. */
export const ALLOWED_TENANT_MODES: Record<ServerType, readonly TenantMode[]> = {
  postgres: ["schema", "database"],
  hacker: ["schema", "database"],
  clickhouse: ["database", "column"],
  mongo: ["database"],
  ethie: ["column"],
};
