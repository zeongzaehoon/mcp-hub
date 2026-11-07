import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Field } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { ROLE_LABEL, type Role } from "@/domains/common/enums";
import { useCreateUser } from "@/domains/users/mutations";

interface UserCreateDialogProps {
  companyId: string;
  open: boolean;
  onClose: () => void;
  allowMasterRole: boolean;
}

const MIN_PASSWORD = 10;

export function UserCreateDialog({ companyId, open, onClose, allowMasterRole }: UserCreateDialogProps) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [password, setPassword] = useState("");
  const create = useCreateUser(companyId);

  useEffect(() => {
    if (open) {
      setEmail("");
      setName("");
      setRole("member");
      setPassword("");
      create.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const needsPassword = role !== "member";
  const passwordOk = !needsPassword || password.length >= MIN_PASSWORD;
  const canSubmit = email.trim().length > 3 && name.trim().length > 0 && passwordOk && !create.isPending;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    create.mutate(
      {
        email: email.trim(),
        name: name.trim(),
        role,
        ...(needsPassword ? { password } : {}),
      },
      { onSuccess: onClose },
    );
  };

  const roles: Role[] = allowMasterRole ? ["admin", "member", "master"] : ["admin", "member"];

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="사용자 추가"
      locked={create.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={create.isPending}>
            취소
          </Button>
          <Button type="submit" form="user-create-form" loading={create.isPending} disabled={!canSubmit}>
            추가
          </Button>
        </>
      }
    >
      <form id="user-create-form" onSubmit={onSubmit} className="space-y-4">
        <Field label="이메일" htmlFor="user-email">
          <Input id="user-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
        </Field>
        <Field label="이름" htmlFor="user-name">
          <Input id="user-name" value={name} onChange={(e) => setName(e.target.value)} required />
        </Field>
        <Field label="역할" htmlFor="user-role" hint="멤버는 API 키 소유자이며 콘솔에 로그인할 수 없습니다. 관리자는 로그인용 비밀번호가 필요합니다.">
          <Select id="user-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {roles.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </Select>
        </Field>
        {needsPassword ? (
          <Field label="비밀번호" htmlFor="user-password" hint={`최소 ${MIN_PASSWORD}자`}>
            <Input
              id="user-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={MIN_PASSWORD}
              required
            />
          </Field>
        ) : null}
        <ErrorAlert error={create.error} />
      </form>
    </Dialog>
  );
}
