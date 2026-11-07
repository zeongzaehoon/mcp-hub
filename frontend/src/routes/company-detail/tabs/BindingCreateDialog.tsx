import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Field, FieldHint } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Spinner } from "@/components/ui/Spinner";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { ALLOWED_TENANT_MODES, TENANT_MODE_LABEL, type TenantMode } from "@/domains/common/enums";
import { useCreateBinding } from "@/domains/bindings/mutations";
import { IDENT_PATTERN, type TenantCredentialsIn, type TenantSpecIn } from "@/domains/bindings/types";
import { useServersPage } from "@/domains/servers/queries";

interface BindingCreateDialogProps {
  companyId: string;
  open: boolean;
  onClose: () => void;
  existingServerIds: string[];
}

const IDENT_HINT = "최대 63자. 따옴표·백틱·백슬래시·세미콜론 불가 (대소문자·하이픈·공백 허용, 서버에서 항상 인용 처리)";

export function BindingCreateDialog({ companyId, open, onClose, existingServerIds }: BindingCreateDialogProps) {
  const serversQuery = useServersPage({ status: "active" }, open);
  const [serverId, setServerId] = useState("");
  const [mode, setMode] = useState<TenantMode>("schema");
  const [schemaName, setSchemaName] = useState("");
  const [database, setDatabase] = useState("");
  const [column, setColumn] = useState("");
  const [value, setValue] = useState("");
  const [columnDb, setColumnDb] = useState("");
  const [useCredentials, setUseCredentials] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const create = useCreateBinding(companyId);

  const existing = useMemo(() => new Set(existingServerIds), [existingServerIds]);
  const candidates = useMemo(
    () => (serversQuery.data?.items ?? []).filter((s) => !existing.has(s.server_id)),
    [serversQuery.data, existing],
  );
  const server = useMemo(() => candidates.find((s) => s.server_id === serverId), [candidates, serverId]);
  const allowedModes: readonly TenantMode[] = server ? ALLOWED_TENANT_MODES[server.server_type] : [];

  useEffect(() => {
    if (open) {
      setServerId("");
      setMode("schema");
      setSchemaName("");
      setDatabase("");
      setColumn("");
      setValue("");
      setColumnDb("");
      setUseCredentials(false);
      setUsername("");
      setPassword("");
      create.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // keep mode valid for the selected server type
  useEffect(() => {
    if (allowedModes.length > 0 && !allowedModes.includes(mode)) setMode(allowedModes[0]!);
  }, [allowedModes, mode]);

  const identOk = (v: string) => IDENT_PATTERN.test(v);
  const tenantValid =
    mode === "schema"
      ? identOk(schemaName)
      : mode === "database"
        ? identOk(database)
        : identOk(column) && value.length > 0 && value.length <= 128 && (columnDb.trim() === "" || identOk(columnDb.trim()));
  const credsValid = !useCredentials || (username.trim().length > 0 && password.length > 0);
  const canSubmit = Boolean(server) && allowedModes.includes(mode) && tenantValid && credsValid && !create.isPending;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit || !server) return;
    const credentials: TenantCredentialsIn | undefined = useCredentials ? { username: username.trim(), password } : undefined;
    let tenant: TenantSpecIn;
    if (mode === "schema") tenant = { mode, schema_name: schemaName, ...(credentials ? { credentials } : {}) };
    else if (mode === "database") tenant = { mode, database, ...(credentials ? { credentials } : {}) };
    else tenant = { mode, column, value, ...(columnDb.trim() ? { database: columnDb.trim() } : {}), ...(credentials ? { credentials } : {}) };
    create.mutate({ server_id: server.server_id, tenant }, { onSuccess: onClose });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="서버 바인딩 추가"
      description="회사와 MCP 서버를 연결하고 데이터 격리 방식을 지정합니다."
      locked={create.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={create.isPending}>
            취소
          </Button>
          <Button type="submit" form="binding-create-form" loading={create.isPending} disabled={!canSubmit}>
            추가
          </Button>
        </>
      }
    >
      <form id="binding-create-form" onSubmit={onSubmit} className="space-y-4">
        <Field label="MCP 서버" htmlFor="binding-server" hint={candidates.length === 0 && !serversQuery.isPending ? "바인딩 가능한 활성 서버가 없습니다" : undefined}>
          {serversQuery.isPending ? (
            <Spinner />
          ) : (
            <Select id="binding-server" value={serverId} onChange={(e) => setServerId(e.target.value)} required>
              <option value="">서버 선택</option>
              {candidates.map((s) => (
                <option key={s.server_id} value={s.server_id}>
                  {s.name} ({s.server_type}) — {s.server_id}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <ErrorAlert error={serversQuery.error} />

        {server ? (
          <>
            <Field label="격리 방식" htmlFor="binding-mode" hint={`${server.server_type} 서버 지원: ${allowedModes.join(", ")}`}>
              <Select id="binding-mode" value={mode} onChange={(e) => setMode(e.target.value as TenantMode)}>
                {allowedModes.map((m) => (
                  <option key={m} value={m}>
                    {TENANT_MODE_LABEL[m]} ({m})
                  </option>
                ))}
              </Select>
            </Field>

            {mode === "schema" ? (
              <Field label="스키마 이름" htmlFor="binding-schema" hint={IDENT_HINT}>
                <Input id="binding-schema" value={schemaName} onChange={(e) => setSchemaName(e.target.value.trim())} placeholder="acme" />
              </Field>
            ) : null}
            {mode === "database" ? (
              <Field label="데이터베이스 이름" htmlFor="binding-database" hint={IDENT_HINT}>
                <Input id="binding-database" value={database} onChange={(e) => setDatabase(e.target.value.trim())} placeholder="acme_db" />
              </Field>
            ) : null}
            {mode === "column" ? (
              <div className="grid grid-cols-2 gap-3">
                <Field label="컬럼" htmlFor="binding-column" hint={IDENT_HINT}>
                  <Input id="binding-column" value={column} onChange={(e) => setColumn(e.target.value.trim())} placeholder="company_id" />
                </Field>
                <Field label="값" htmlFor="binding-value" hint="1~128자">
                  <Input id="binding-value" value={value} onChange={(e) => setValue(e.target.value)} placeholder={companyId} maxLength={128} />
                </Field>
                <Field label="데이터베이스 (선택)" htmlFor="binding-column-db" hint="공유 테이블이 있는 DB. 비우면 서버 기본 DB">
                  <Input id="binding-column-db" value={columnDb} onChange={(e) => setColumnDb(e.target.value)} placeholder="analytics" />
                </Field>
              </div>
            ) : null}

            <div className="space-y-3 rounded-[5px] border border-border p-3">
              <Checkbox
                label="전용 데이터소스 자격증명 사용"
                description="미설정 시 서버의 기본 연결 계정을 사용합니다. 비밀번호는 암호화되어 저장됩니다."
                checked={useCredentials}
                onChange={(e) => setUseCredentials(e.target.checked)}
                className="border-0 px-0 py-0 hover:bg-transparent"
              />
              {useCredentials ? (
                <div className="grid grid-cols-2 gap-3">
                  <Field label="사용자명" htmlFor="binding-cred-user">
                    <Input id="binding-cred-user" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
                  </Field>
                  <Field label="비밀번호" htmlFor="binding-cred-pass">
                    <Input id="binding-cred-pass" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
                  </Field>
                </div>
              ) : null}
            </div>
          </>
        ) : (
          <FieldHint>서버를 선택하면 서버 타입에 맞는 격리 방식을 설정할 수 있습니다.</FieldHint>
        )}
        <ErrorAlert error={create.error} />
      </form>
    </Dialog>
  );
}
