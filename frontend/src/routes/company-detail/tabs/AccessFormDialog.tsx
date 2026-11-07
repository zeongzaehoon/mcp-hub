import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Field } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Spinner } from "@/components/ui/Spinner";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import type { Permission } from "@/domains/common/enums";
import { useCreateAccess, usePatchAccess } from "@/domains/access/mutations";
import { useUserAccessPage } from "@/domains/access/queries";
import type { Access, AccessPatch } from "@/domains/access/types";
import type { Binding } from "@/domains/bindings/types";
import type { Server } from "@/domains/servers/types";
import type { User } from "@/domains/users/types";
import { PermissionToolsPicker, sameSet, toolsCovered, useToolMap } from "./PermissionToolsPicker";

interface AccessFormDialogProps {
  mode: "create" | "edit";
  companyId: string;
  open: boolean;
  onClose: () => void;
  /** edit only */
  editing?: Access;
  /** create only: preselected user (e.g. opened from a user row) */
  presetUserId?: string;
  activeUsers: User[];
  activeBindings: Binding[];
  serversById: Map<string, Server>;
  usersById: Map<string, User>;
  pickersLoading: boolean;
}

export function AccessFormDialog(p: AccessFormDialogProps) {
  const isEdit = p.mode === "edit";
  const [userId, setUserId] = useState("");
  const [serverId, setServerId] = useState("");
  const [permissions, setPermissions] = useState<Permission[]>(["read"]);
  const [tools, setTools] = useState<string[]>([]);

  const create = useCreateAccess(p.companyId);
  const patch = usePatchAccess(p.companyId);
  // servers this user already has an access record for (any status) cannot be granted again
  const userAccess = useUserAccessPage(p.companyId, userId, p.open && !isEdit);
  const pending = create.isPending || patch.isPending;
  const error = isEdit ? patch.error : create.error;

  // reset form on open
  useEffect(() => {
    if (!p.open) return;
    if (isEdit && p.editing) {
      setUserId(p.editing.user_id);
      setServerId(p.editing.server_id);
      setPermissions([...p.editing.permissions]);
      setTools([...p.editing.allowed_tools]);
    } else {
      setUserId(p.presetUserId ?? "");
      setServerId("");
      setPermissions(["read"]);
      setTools([]);
    }
    create.reset();
    patch.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.open, isEdit, p.editing?.user_id, p.editing?.server_id, p.presetUserId]);

  const grantedServerIds = useMemo(() => new Set((userAccess.data?.items ?? []).map((a) => a.server_id)), [userAccess.data]);
  const candidateBindings = useMemo(
    () => p.activeBindings.filter((b) => !grantedServerIds.has(b.server_id)),
    [p.activeBindings, grantedServerIds],
  );

  // drop a server selection that became invalid (e.g. user changed to one who already has it)
  useEffect(() => {
    if (!isEdit && serverId && !candidateBindings.some((b) => b.server_id === serverId)) setServerId("");
  }, [isEdit, serverId, candidateBindings]);

  const binding = useMemo(() => p.activeBindings.find((b) => b.server_id === serverId), [p.activeBindings, serverId]);
  const server = p.serversById.get(serverId);
  const serverType = server?.server_type ?? binding?.server_type ?? p.editing?.server_type;
  const { toolMap, toolEntries, catalogPending } = useToolMap(server, serverType);

  const canSubmit =
    permissions.length > 0 &&
    tools.length > 0 &&
    toolsCovered(tools, permissions, toolMap) &&
    (isEdit || (userId !== "" && serverId !== "" && !userAccess.isPending)) &&
    !pending;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    if (isEdit) {
      if (!p.editing) return;
      const body: AccessPatch = {};
      if (!sameSet(permissions, p.editing.permissions)) body.permissions = permissions;
      if (!sameSet(tools, p.editing.allowed_tools)) body.allowed_tools = tools;
      if (Object.keys(body).length === 0) {
        p.onClose();
        return;
      }
      patch.mutate({ userId: p.editing.user_id, serverId: p.editing.server_id, body }, { onSuccess: p.onClose });
    } else {
      create.mutate({ userId, body: { server_id: serverId, permissions, allowed_tools: tools } }, { onSuccess: p.onClose });
    }
  };

  const formId = isEdit ? "access-edit-form" : "access-create-form";
  const editingUser = p.editing ? p.usersById.get(p.editing.user_id) : undefined;
  const editingServer = p.editing ? p.serversById.get(p.editing.server_id) : undefined;

  return (
    <Dialog
      open={p.open}
      onClose={p.onClose}
      title={isEdit ? "서버 접근 편집" : "서버 접근 부여"}
      description={
        isEdit && p.editing ? (
          <span>
            {editingUser?.email ?? p.editing.user_id} → {editingServer?.name ?? p.editing.server_id} · 변경 사항은 이 사용자의 모든 API 키에 즉시 반영됩니다.
          </span>
        ) : (
          "사용자가 MCP 서버에서 쓸 수 있는 권한과 도구를 지정합니다. API 키는 이 범위 안에서만 동작합니다."
        )
      }
      size="lg"
      locked={pending}
      footer={
        <>
          <Button variant="outline" onClick={p.onClose} disabled={pending}>
            취소
          </Button>
          <Button type="submit" form={formId} loading={pending} disabled={!canSubmit}>
            {isEdit ? "저장" : "부여"}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={onSubmit} className="space-y-5">
        {!isEdit ? (
          p.pickersLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner className="h-4 w-4" /> 사용자·서버 목록 불러오는 중
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="사용자" htmlFor={`${formId}-user`} hint={p.activeUsers.length === 0 ? "활성 사용자가 없습니다. 먼저 사용자를 추가하세요." : undefined}>
                <Select id={`${formId}-user`} value={userId} onChange={(e) => setUserId(e.target.value)} required>
                  <option value="">사용자 선택</option>
                  {p.activeUsers.map((u) => (
                    <option key={u.user_id} value={u.user_id}>
                      {u.email} ({u.name})
                    </option>
                  ))}
                </Select>
              </Field>
              <Field
                label="대상 서버"
                htmlFor={`${formId}-server`}
                hint={
                  p.activeBindings.length === 0
                    ? "활성 바인딩이 있는 서버가 없습니다."
                    : userId && !userAccess.isPending && candidateBindings.length === 0
                      ? "이 사용자는 바인딩된 모든 서버에 이미 접근 권한이 있습니다."
                      : "활성 바인딩이 있고 아직 부여되지 않은 서버만 표시됩니다"
                }
              >
                <Select id={`${formId}-server`} value={serverId} onChange={(e) => setServerId(e.target.value)} required disabled={!userId || userAccess.isPending}>
                  <option value="">{!userId ? "사용자를 먼저 선택" : userAccess.isPending ? "불러오는 중…" : "서버 선택"}</option>
                  {candidateBindings.map((b) => {
                    const s = p.serversById.get(b.server_id);
                    return (
                      <option key={b.server_id} value={b.server_id}>
                        {s?.name ?? b.server_id} ({b.server_type})
                      </option>
                    );
                  })}
                </Select>
              </Field>
            </div>
          )
        ) : null}
        {!isEdit ? <ErrorAlert error={userAccess.error} /> : null}

        <PermissionToolsPicker
          serverType={serverType}
          toolMap={toolMap}
          toolEntries={toolEntries}
          catalogPending={catalogPending}
          permissions={permissions}
          tools={tools}
          onPermissionsChange={setPermissions}
          onToolsChange={setTools}
        />
        <ErrorAlert error={error} />
      </form>
    </Dialog>
  );
}
