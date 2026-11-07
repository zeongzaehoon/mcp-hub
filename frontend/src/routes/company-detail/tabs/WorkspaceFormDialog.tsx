import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Code } from "@/components/ui/Code";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Field } from "@/components/ui/Label";
import { Textarea } from "@/components/ui/Textarea";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { usePutWorkspace } from "@/domains/workspace/mutations";
import type { WorkspaceOut } from "@/domains/workspace/types";

interface WorkspaceFormDialogProps {
  companyId: string;
  /** existing connection (replace credentials / change admin) or null (first connect) */
  current: WorkspaceOut | null;
  open: boolean;
  onClose: () => void;
}

interface ParsedKey {
  client_email: string;
  client_id: string;
}

/** Client-side look at the pasted JSON: only `type: service_account` with a PEM private key is sent. Nothing from the
 * key itself is kept in state beyond the raw textarea value, which is cleared after submit. */
function parseServiceAccount(text: string): { key: ParsedKey | null; error: string | null } {
  const t = text.trim();
  if (!t) return { key: null, error: null };
  let obj: unknown;
  try {
    obj = JSON.parse(t);
  } catch {
    return { key: null, error: "JSON 형식이 아닙니다. 다운로드한 키 파일의 내용을 그대로 붙여 넣으세요." };
  }
  if (!obj || typeof obj !== "object") return { key: null, error: "JSON 객체가 아닙니다." };
  const o = obj as Record<string, unknown>;
  if (o.type !== "service_account") return { key: null, error: "서비스 계정 키가 아닙니다 (type 이 service_account 여야 합니다)." };
  if (typeof o.private_key !== "string" || !o.private_key.includes("PRIVATE KEY")) return { key: null, error: "private_key 가 없습니다." };
  if (typeof o.client_email !== "string" || typeof o.client_id !== "string") return { key: null, error: "client_email / client_id 가 없습니다." };
  return { key: { client_email: o.client_email, client_id: o.client_id }, error: null };
}

function emailDomain(email: string): string {
  const at = email.lastIndexOf("@");
  return at < 0 ? "" : email.slice(at + 1).toLowerCase();
}

export function WorkspaceFormDialog({ companyId, current, open, onClose }: WorkspaceFormDialogProps) {
  const [domain, setDomain] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [json, setJson] = useState("");
  const put = usePutWorkspace(companyId);
  const editing = current !== null;

  useEffect(() => {
    if (!open) return;
    setDomain(current?.domain ?? "");
    setAdminEmail(current?.admin_email ?? "");
    setJson("");
    put.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, current]);

  const parsed = useMemo(() => parseServiceAccount(json), [json]);
  const normDomain = domain.trim().toLowerCase();
  const normAdmin = adminEmail.trim().toLowerCase();
  const domainOk = /^[a-z0-9.-]+\.[a-z]{2,}$/.test(normDomain);
  const adminError =
    normAdmin && !normAdmin.includes("@")
      ? "이메일 형식이 아닙니다."
      : normAdmin && domainOk && emailDomain(normAdmin) !== normDomain
        ? `${normDomain} 도메인의 계정이어야 합니다.`
        : null;
  const keyRequired = !editing && json.trim() === "";
  const canSubmit = domainOk && !!normAdmin && !adminError && !parsed.error && !keyRequired && !put.isPending;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    put.mutate(
      { domain: normDomain, admin_email: normAdmin, service_account_json: json.trim() ? json.trim() : null },
      {
        onSuccess: () => {
          setJson(""); // the private key never lingers in state
          onClose();
        },
      },
    );
  };

  const formId = "workspace-form";
  return (
    <Dialog
      open={open}
      onClose={onClose}
      locked={put.isPending}
      size="lg"
      title={editing ? "서비스 계정 교체" : "Google Workspace 연동"}
      description="Google Cloud 서비스 계정 키로 Workspace 디렉터리(사용자·그룹)를 읽어 옵니다. 키는 암호화되어 저장되며 다시 표시되지 않습니다."
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={put.isPending}>
            취소
          </Button>
          <Button type="submit" form={formId} loading={put.isPending} disabled={!canSubmit} data-testid="workspace-form-submit">
            {editing ? "저장" : "등록"}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={onSubmit} className="space-y-5" data-testid="workspace-form">
        <Field label="도메인" htmlFor="workspace-form-domain" required hint="Workspace 기본 도메인 (예: example.com). 이 도메인의 디렉터리만 동기화합니다.">
          <Input
            id="workspace-form-domain"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            placeholder="example.com"
            autoComplete="off"
            className="font-mono"
            invalid={!!normDomain && !domainOk}
            required
          />
        </Field>
        <Field
          label="위임 대상 관리자 이메일"
          htmlFor="workspace-form-admin-email"
          required
          error={adminError}
          hint="서비스 계정이 이 관리자 명의로 디렉터리를 읽습니다. 도메인 전체 위임이 허용된 Workspace 관리자여야 합니다."
        >
          <Input
            id="workspace-form-admin-email"
            type="email"
            value={adminEmail}
            onChange={(e) => setAdminEmail(e.target.value)}
            placeholder={normDomain ? `admin@${normDomain}` : "admin@example.com"}
            autoComplete="off"
            invalid={!!adminError}
            required
          />
        </Field>
        <Field
          label="서비스 계정 JSON"
          htmlFor="workspace-form-json"
          required={!editing}
          error={parsed.error}
          hint={
            parsed.key ? (
              <span className="inline-flex flex-wrap items-center gap-1.5">
                감지됨: <Code>{parsed.key.client_email}</Code> · 클라이언트 ID <Code>{parsed.key.client_id}</Code>
              </span>
            ) : editing ? (
              "비워 두면 저장된 키를 유지합니다. 새 키를 붙여 넣으면 교체됩니다."
            ) : (
              "Google Cloud 콘솔 → IAM 및 관리자 → 서비스 계정 → 키 → JSON 키 만들기로 내려받은 파일의 내용입니다."
            )
          }
        >
          <Textarea
            id="workspace-form-json"
            value={json}
            onChange={(e) => setJson(e.target.value)}
            placeholder={editing ? "비워 두면 저장된 키를 유지합니다" : '{ "type": "service_account", ... }'}
            autoComplete="off"
            spellCheck={false}
            rows={7}
            className="font-mono text-xs"
            invalid={!!parsed.error}
            required={!editing}
          />
        </Field>
        <ErrorAlert error={put.error} />
      </form>
    </Dialog>
  );
}
