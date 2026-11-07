import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Field } from "@/components/ui/Label";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { usePatchUser } from "@/domains/users/mutations";
import type { User } from "@/domains/users/types";

const MIN_PASSWORD = 10;

interface UserPasswordDialogProps {
  companyId: string;
  user: User | null;
  onClose: () => void;
}

export function UserPasswordDialog({ companyId, user, onClose }: UserPasswordDialogProps) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const patch = usePatchUser(companyId);
  const open = user !== null;

  useEffect(() => {
    if (open) {
      setPassword("");
      setConfirm("");
      patch.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, user?.user_id]);

  const mismatch = confirm.length > 0 && confirm !== password;
  const canSubmit = password.length >= MIN_PASSWORD && confirm === password && !patch.isPending;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!user || !canSubmit) return;
    patch.mutate({ userId: user.user_id, body: { password } }, { onSuccess: onClose });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="비밀번호 설정"
      description={user ? `${user.email} 계정의 로그인 비밀번호를 설정합니다.` : undefined}
      size="sm"
      locked={patch.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={patch.isPending}>
            취소
          </Button>
          <Button type="submit" form="user-password-form" loading={patch.isPending} disabled={!canSubmit}>
            저장
          </Button>
        </>
      }
    >
      <form id="user-password-form" onSubmit={onSubmit} className="space-y-4">
        <Field label="새 비밀번호" htmlFor="pw-new" hint={`최소 ${MIN_PASSWORD}자`}>
          <Input
            id="pw-new"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={MIN_PASSWORD}
            autoFocus
          />
        </Field>
        <Field label="비밀번호 확인" htmlFor="pw-confirm" hint={mismatch ? "비밀번호가 일치하지 않습니다" : undefined}>
          <Input
            id="pw-confirm"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            className={mismatch ? "border-destructive" : undefined}
          />
        </Field>
        <ErrorAlert error={patch.error} />
      </form>
    </Dialog>
  );
}
