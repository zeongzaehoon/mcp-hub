import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Field } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Spinner } from "@/components/ui/Spinner";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import type { Permission } from "@/domains/common/enums";
import type { Binding } from "@/domains/bindings/types";
import { useCreatePolicy, usePatchPolicy } from "@/domains/policies/mutations";
import type { Policy, PolicyPatch } from "@/domains/policies/types";
import type { Server } from "@/domains/servers/types";
import type { Team } from "@/domains/workspace/types";
import { PermissionToolsPicker, sameSet, toolsCovered, useToolMap } from "./PermissionToolsPicker";

/** `<select>` value for the "everyone" target (team_id null). */
const EVERYONE = "*";

interface PolicyFormDialogProps {
  mode: "create" | "edit";
  companyId: string;
  open: boolean;
  onClose: () => void;
  /** edit only */
  editing?: Policy;
  /** create: existing policies, to hide team/server pairs that already have one (the backend 409s on them) */
  policies: Policy[];
  activeTeams: Team[];
  activeBindings: Binding[];
  serversById: Map<string, Server>;
  teamsById: Map<string, Team>;
  pickersLoading: boolean;
}

export function PolicyFormDialog(p: PolicyFormDialogProps) {
  const isEdit = p.mode === "edit";
  const [target, setTarget] = useState(EVERYONE);
  const [serverId, setServerId] = useState("");
  const [permissions, setPermissions] = useState<Permission[]>(["read"]);
  const [tools, setTools] = useState<string[]>([]);

  const create = useCreatePolicy(p.companyId);
  const patch = usePatchPolicy(p.companyId);
  const pending = create.isPending || patch.isPending;
  const error = isEdit ? patch.error : create.error;

  // reset form on open
  useEffect(() => {
    if (!p.open) return;
    if (isEdit && p.editing) {
      setTarget(p.editing.team_id ?? EVERYONE);
      setServerId(p.editing.server_id);
      setPermissions([...p.editing.permissions]);
      setTools([...p.editing.allowed_tools]);
    } else {
      setTarget(EVERYONE);
      setServerId("");
      setPermissions(["read"]);
      setTools([]);
    }
    create.reset();
    patch.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.open, isEdit, p.editing?.policy_id]);

  const teamId = target === EVERYONE ? null : target;
  // servers that already have a policy for this target (any status) cannot get another one
  const takenServerIds = useMemo(
    () => new Set(p.policies.filter((x) => x.team_id === teamId).map((x) => x.server_id)),
    [p.policies, teamId],
  );
  const candidateBindings = useMemo(
    () => p.activeBindings.filter((b) => !takenServerIds.has(b.server_id)),
    [p.activeBindings, takenServerIds],
  );

  // drop a server selection that became invalid (e.g. target changed to one that already has it)
  useEffect(() => {
    if (!isEdit && serverId && !candidateBindings.some((b) => b.server_id === serverId)) setServerId("");
  }, [isEdit, serverId, candidateBindings]);

  const binding = useMemo(() => p.activeBindings.find((b) => b.server_id === serverId), [p.activeBindings, serverId]);
  const server = p.serversById.get(serverId);
  const serverType = server?.server_type ?? binding?.server_type ?? p.editing?.server_type;
  const { toolMap, toolEntries, catalogPending } = useToolMap(server, serverType);

  const canSubmit = permissions.length > 0 && tools.length > 0 && toolsCovered(tools, permissions, toolMap) && (isEdit || serverId !== "") && !pending;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    if (isEdit) {
      if (!p.editing) return;
      const body: PolicyPatch = {};
      if (!sameSet(permissions, p.editing.permissions)) body.permissions = permissions;
      if (!sameSet(tools, p.editing.allowed_tools)) body.allowed_tools = tools;
      if (Object.keys(body).length === 0) {
        p.onClose();
        return;
      }
      patch.mutate({ policyId: p.editing.policy_id, body }, { onSuccess: p.onClose });
    } else {
      create.mutate({ team_id: teamId, server_id: serverId, permissions, allowed_tools: tools }, { onSuccess: p.onClose });
    }
  };

  const formId = isEdit ? "policy-edit-form" : "policy-create-form";
  const targetLabel = (id: string | null) => (id === null ? "전원" : (p.teamsById.get(id)?.name ?? id));
  const editingServer = p.editing ? p.serversById.get(p.editing.server_id) : undefined;

  return (
    <Dialog
      open={p.open}
      onClose={p.onClose}
      title={isEdit ? "팀 정책 편집" : "팀 정책 추가"}
      description={
        isEdit && p.editing ? (
          <span>
            {targetLabel(p.editing.team_id)} → {editingServer?.name ?? p.editing.server_id} · 저장하면 이 정책이 만든 사용자 접근에 즉시 반영됩니다.
          </span>
        ) : (
          "팀(또는 전원)에게 MCP 서버 권한·도구를 부여합니다. 정책은 팀 멤버마다 사용자 접근으로 즉시 적용되며, 멤버가 바뀌면 다시 적용됩니다."
        )
      }
      size="lg"
      locked={pending}
      footer={
        <>
          <Button variant="outline" onClick={p.onClose} disabled={pending}>
            취소
          </Button>
          <Button type="submit" form={formId} loading={pending} disabled={!canSubmit} data-testid="policy-form-submit">
            {isEdit ? "저장" : "추가"}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={onSubmit} className="space-y-5" data-testid="policy-form">
        {!isEdit ? (
          p.pickersLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner className="h-4 w-4" /> 팀·서버 목록 불러오는 중
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="대상" htmlFor={`${formId}-target`} hint={p.activeTeams.length === 0 ? "활성 팀이 없습니다. 전원 정책만 만들 수 있습니다." : undefined}>
                <Select id={`${formId}-target`} value={target} onChange={(e) => setTarget(e.target.value)} required>
                  <option value={EVERYONE}>전원 (회사의 모든 활성 사용자)</option>
                  {p.activeTeams.map((t) => (
                    <option key={t.team_id} value={t.team_id}>
                      {t.name} ({t.source === "workspace" ? "Workspace" : "수동"})
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
                    : candidateBindings.length === 0
                      ? "이 대상은 바인딩된 모든 서버에 이미 정책이 있습니다."
                      : "활성 바인딩이 있고 이 대상에 아직 정책이 없는 서버만 표시됩니다"
                }
              >
                <Select id={`${formId}-server`} value={serverId} onChange={(e) => setServerId(e.target.value)} required>
                  <option value="">서버 선택</option>
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
