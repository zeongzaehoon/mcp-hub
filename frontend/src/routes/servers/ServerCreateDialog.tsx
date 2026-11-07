import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Field } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { SERVER_TYPE_VALUES, type ServerType } from "@/domains/common/enums";
import { useCreateServer } from "@/domains/servers/mutations";
import { DSN_PLACEHOLDER, type Server } from "@/domains/servers/types";

interface ServerCreateDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (s: Server) => void;
}

export function ServerCreateDialog({ open, onClose, onCreated }: ServerCreateDialogProps) {
  const [serverType, setServerType] = useState<ServerType>("postgres");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [dsn, setDsn] = useState("");
  const create = useCreateServer();

  useEffect(() => {
    if (open) {
      setServerType("postgres");
      setName("");
      setDescription("");
      setEndpoint("");
      setDsn("");
      create.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const canSubmit = name.trim().length > 0 && !create.isPending;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    create.mutate(
      { server_type: serverType, name: name.trim(), description: description.trim(), endpoint: endpoint.trim(), ...(dsn.trim() ? { dsn: dsn.trim() } : {}) },
      { onSuccess: onCreated },
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="MCP 서버 등록"
      locked={create.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={create.isPending}>
            취소
          </Button>
          <Button type="submit" form="server-create-form" loading={create.isPending} disabled={!canSubmit}>
            등록
          </Button>
        </>
      }
    >
      <form id="server-create-form" onSubmit={onSubmit} className="space-y-4">
        <Field label="서버 타입" htmlFor="server-type" hint="타입에 따라 제공 도구 카탈로그와 허용 테넌트 격리 방식이 결정됩니다">
          <Select id="server-type" value={serverType} onChange={(e) => setServerType(e.target.value as ServerType)}>
            {SERVER_TYPE_VALUES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="이름" htmlFor="server-name">
          <Input id="server-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="analytics-clickhouse" autoFocus required />
        </Field>
        <Field label="설명" htmlFor="server-desc">
          <Input id="server-desc" value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label="엔드포인트" htmlFor="server-endpoint" hint="MCP 서버 URL (예: http://mcp-postgres:9001)">
          <Input id="server-endpoint" value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="http://" />
        </Field>
        <Field label="데이터소스 연결 (선택)" htmlFor="server-dsn" hint="암호화되어 저장되며 다시 표시되지 않습니다. 비우면 MCP 프로세스의 기본 연결을 사용합니다.">
          <Input id="server-dsn" type="password" value={dsn} onChange={(e) => setDsn(e.target.value)} placeholder={DSN_PLACEHOLDER[serverType]} autoComplete="off" />
        </Field>
        <ErrorAlert error={create.error} />
      </form>
    </Dialog>
  );
}
