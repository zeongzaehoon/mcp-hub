import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Field, FieldHint } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Spinner } from "@/components/ui/Spinner";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { fromDatetimeLocal, toDatetimeLocal } from "@/lib/utils";
import { PERMISSION_LABEL } from "@/domains/common/enums";
import { useUserAccessPage } from "@/domains/access/queries";
import type { Access } from "@/domains/access/types";
import { useCreateApiKey, usePatchApiKey } from "@/domains/apikeys/mutations";
import type { ApiKey, ApiKeyIssued, ApiKeyPatch } from "@/domains/apikeys/types";
import type { Binding } from "@/domains/bindings/types";
import type { Server } from "@/domains/servers/types";
import type { User } from "@/domains/users/types";

type ScopeMode = "all" | "selected";

interface ApiKeyFormDialogProps {
  mode: "create" | "edit";
  companyId: string;
  open: boolean;
  onClose: () => void;
  /** create only */
  onIssued?: (issued: ApiKeyIssued) => void;
  /** edit only */
  editing?: ApiKey;
  activeUsers: User[];
  activeBindings: Binding[];
  serversById: Map<string, Server>;
  pickersLoading: boolean;
}

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  const sb = new Set(b);
  return a.every((x) => sb.has(x));
}

interface ScopeOption {
  serverId: string;
  access: Access | undefined;
  /** the user can currently use this server (active access + active binding) */
  usable: boolean;
}

export function ApiKeyFormDialog(p: ApiKeyFormDialogProps) {
  const isEdit = p.mode === "edit";
  const [name, setName] = useState("");
  const [userId, setUserId] = useState("");
  const [scopeMode, setScopeMode] = useState<ScopeMode>("all");
  const [scopeIds, setScopeIds] = useState<string[]>([]);
  const [expiresLocal, setExpiresLocal] = useState("");
  const [rateLimit, setRateLimit] = useState(""); // "" => server default

  const create = useCreateApiKey(p.companyId);
  const patch = usePatchApiKey(p.companyId);
  const userAccess = useUserAccessPage(p.companyId, userId, p.open);
  const pending = create.isPending || patch.isPending;
  const error = isEdit ? patch.error : create.error;

  // reset form on open
  useEffect(() => {
    if (!p.open) return;
    if (isEdit && p.editing) {
      setName(p.editing.name);
      setUserId(p.editing.user_id);
      setScopeMode(p.editing.server_ids === null ? "all" : "selected");
      setScopeIds(p.editing.server_ids ? [...p.editing.server_ids] : []);
      setExpiresLocal(toDatetimeLocal(p.editing.expires_at));
      setRateLimit(p.editing.rate_limit_per_minute == null ? "" : String(p.editing.rate_limit_per_minute));
    } else {
      setName("");
      setUserId("");
      setScopeMode("all");
      setScopeIds([]);
      setExpiresLocal("");
      setRateLimit("");
    }
    create.reset();
    patch.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.open, isEdit, p.editing?.key_id]);

  // a different owner has different accesses: drop the previous selection (create only; owner is fixed on edit)
  const changeUser = (next: string) => {
    setUserId(next);
    setScopeIds([]);
  };

  const activeBindingIds = useMemo(() => new Set(p.activeBindings.map((b) => b.server_id)), [p.activeBindings]);

  /** Servers the user can use, plus (on edit) servers already in the key's scope even if no longer usable, so they can be removed. */
  const scopeOptions: ScopeOption[] = useMemo(() => {
    const byServer = new Map<string, Access>();
    for (const a of userAccess.data?.items ?? []) byServer.set(a.server_id, a);
    const opts = new Map<string, ScopeOption>();
    for (const a of byServer.values()) {
      const usable = a.status === "active" && activeBindingIds.has(a.server_id);
      if (usable) opts.set(a.server_id, { serverId: a.server_id, access: a, usable });
    }
    for (const sid of isEdit ? (p.editing?.server_ids ?? []) : []) {
      if (!opts.has(sid)) opts.set(sid, { serverId: sid, access: byServer.get(sid), usable: false });
    }
    return Array.from(opts.values()).sort((a, b) => a.serverId.localeCompare(b.serverId));
  }, [userAccess.data, activeBindingIds, isEdit, p.editing?.server_ids]);
  const usableCount = scopeOptions.filter((o) => o.usable).length;

  const toggleScope = (sid: string, checked: boolean) => {
    setScopeIds((prev) => (checked ? Array.from(new Set([...prev, sid])) : prev.filter((x) => x !== sid)));
  };

  const expiresIso = fromDatetimeLocal(expiresLocal);
  const expiresInPast = expiresIso !== null && new Date(expiresIso).getTime() <= Date.now();
  const rateLimitNum = rateLimit.trim() === "" ? null : Number(rateLimit);
  const rateLimitValid = rateLimitNum == null || (Number.isInteger(rateLimitNum) && rateLimitNum >= 1 && rateLimitNum <= 1_000_000);
  const scopeValid = scopeMode === "all" || scopeIds.length > 0;

  const canSubmit =
    name.trim().length > 0 && scopeValid && !expiresInPast && rateLimitValid && (isEdit || userId !== "") && !pending;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    if (isEdit) {
      if (!p.editing) return;
      const body: ApiKeyPatch = {};
      if (name.trim() !== p.editing.name) body.name = name.trim();
      if (scopeMode === "all") {
        if (p.editing.server_ids !== null) body.scope_all = true;
      } else if (p.editing.server_ids === null || !sameSet(scopeIds, p.editing.server_ids)) {
        body.server_ids = scopeIds;
      }
      const origLocal = toDatetimeLocal(p.editing.expires_at);
      if (expiresLocal !== origLocal) {
        if (expiresIso) body.expires_at = expiresIso;
        else body.clear_expiry = true;
      }
      const origRate = p.editing.rate_limit_per_minute == null ? "" : String(p.editing.rate_limit_per_minute);
      if (rateLimit !== origRate) {
        if (rateLimitNum != null) body.rate_limit_per_minute = rateLimitNum;
        else body.clear_rate_limit = true;
      }
      if (Object.keys(body).length === 0) {
        p.onClose();
        return;
      }
      patch.mutate({ keyId: p.editing.key_id, body }, { onSuccess: p.onClose });
    } else {
      create.mutate(
        {
          user_id: userId,
          name: name.trim(),
          server_ids: scopeMode === "all" ? null : scopeIds,
          ...(expiresIso ? { expires_at: expiresIso } : {}),
          ...(rateLimitNum != null ? { rate_limit_per_minute: rateLimitNum } : {}),
        },
        { onSuccess: (issued) => p.onIssued?.(issued) },
      );
    }
  };

  const formId = isEdit ? "apikey-edit-form" : "apikey-create-form";

  return (
    <Dialog
      open={p.open}
      onClose={p.onClose}
      title={isEdit ? "API 키 편집" : "API 키 발급"}
      description={
        isEdit && p.editing ? (
          <span>{p.editing.key_prefix}… · 소유자는 변경할 수 없습니다. 권한·도구는 소유자의 서버 접근에서 관리합니다.</span>
        ) : (
          "키는 소유 사용자를 대리합니다. 권한·도구는 사용자의 서버 접근을 따르고, 키는 사용할 서버 범위만 좁힐 수 있습니다. 키 값은 발급 직후 한 번만 표시됩니다."
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
            {isEdit ? "저장" : "발급"}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={onSubmit} className="space-y-5">
        <Field label="키 이름" htmlFor={`${formId}-name`} hint="용도를 알 수 있는 이름 (예: BI 대시보드 읽기 전용)">
          <Input id={`${formId}-name`} value={name} onChange={(e) => setName(e.target.value)} autoFocus required />
        </Field>

        {!isEdit ? (
          p.pickersLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner className="h-4 w-4" /> 사용자 목록 불러오는 중
            </div>
          ) : (
            <Field label="소유 사용자" htmlFor={`${formId}-user`} hint={p.activeUsers.length === 0 ? "활성 사용자가 없습니다. 먼저 사용자를 추가하세요." : undefined}>
              <Select id={`${formId}-user`} value={userId} onChange={(e) => changeUser(e.target.value)} required>
                <option value="">사용자 선택</option>
                {p.activeUsers.map((u) => (
                  <option key={u.user_id} value={u.user_id}>
                    {u.email} ({u.name})
                  </option>
                ))}
              </Select>
            </Field>
          )
        ) : null}

        <div className="space-y-2">
          <Field
            label="서버 범위"
            htmlFor={`${formId}-scope`}
            hint={
              scopeMode === "all"
                ? "소유자가 접근 권한을 가진 모든 서버 (나중에 부여되는 서버 포함). MCP URL에 ?server=<server_id>로 서버를 지정합니다."
                : "선택한 서버에서만 동작합니다. 소유자의 활성 서버 접근이 있는 서버만 선택할 수 있습니다."
            }
          >
            <Select id={`${formId}-scope`} value={scopeMode} onChange={(e) => setScopeMode(e.target.value as ScopeMode)} className="w-72">
              <option value="all">전체 서버 (현재·향후)</option>
              <option value="selected">선택한 서버만</option>
            </Select>
          </Field>
          {scopeMode === "selected" ? (
            !userId ? (
              <FieldHint>소유 사용자를 먼저 선택하세요.</FieldHint>
            ) : userAccess.isPending ? (
              <Spinner className="h-4 w-4" />
            ) : scopeOptions.length === 0 ? (
              <Alert variant="warning">이 사용자에게 활성 서버 접근이 없습니다. "서버 접근" 탭에서 먼저 접근을 부여하세요.</Alert>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {scopeOptions.map((o) => {
                  const s = p.serversById.get(o.serverId);
                  const perms = o.access?.permissions.map((x) => PERMISSION_LABEL[x] ?? x).join(", ");
                  return (
                    <Checkbox
                      key={o.serverId}
                      data-server-id={o.serverId}
                      label={
                        <span>
                          {s?.name ?? o.serverId} <span className="text-xs text-muted-foreground">({s?.server_type ?? o.access?.server_type ?? "?"})</span>
                        </span>
                      }
                      description={
                        o.usable
                          ? `권한: ${perms} · 도구 ${o.access?.allowed_tools.length ?? 0}개`
                          : "접근 또는 바인딩이 비활성 — 이 서버에서는 거부됩니다"
                      }
                      checked={scopeIds.includes(o.serverId)}
                      disabled={!o.usable && !scopeIds.includes(o.serverId)}
                      onChange={(e) => toggleScope(o.serverId, e.target.checked)}
                    />
                  );
                })}
              </div>
            )
          ) : userId && !userAccess.isPending && usableCount === 0 ? (
            <Alert variant="warning">이 사용자에게 아직 활성 서버 접근이 없습니다. 발급은 가능하지만 접근을 부여하기 전까지 모든 호출이 거부됩니다.</Alert>
          ) : null}
          {scopeMode === "selected" && userId && scopeOptions.length > 0 && scopeIds.length === 0 ? (
            <FieldHint className="text-destructive">최소 1개의 서버를 선택하세요</FieldHint>
          ) : null}
          <ErrorAlert error={userAccess.error} />
        </div>

        <Field label="분당 호출 한도 (선택)" htmlFor={`${formId}-rate`} hint={rateLimitValid ? "비우면 서버 기본값(600/분) 적용" : "1 이상 1,000,000 이하의 정수"}>
          <Input id={`${formId}-rate`} type="number" min={1} max={1000000} step={1} value={rateLimit} onChange={(e) => setRateLimit(e.target.value)} className="w-40" placeholder="기본값" />
        </Field>

        <Field label="만료 시각 (선택)" htmlFor={`${formId}-expires`} hint={isEdit ? "비우면 무기한으로 변경됩니다" : "비우면 무기한"}>
          <div className="flex items-center gap-2">
            <Input id={`${formId}-expires`} type="datetime-local" value={expiresLocal} onChange={(e) => setExpiresLocal(e.target.value)} className="w-64" />
            {expiresLocal ? (
              <Button variant="ghost" size="sm" onClick={() => setExpiresLocal("")}>
                지우기
              </Button>
            ) : null}
          </div>
        </Field>
        {expiresInPast ? <Alert variant="warning">만료 시각은 미래여야 합니다.</Alert> : null}
        <ErrorAlert error={error} />
      </form>
    </Dialog>
  );
}
