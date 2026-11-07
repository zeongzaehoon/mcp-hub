import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Field } from "@/components/ui/Label";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { useCreateCompany } from "@/domains/companies/mutations";
import { COMPANY_ID_PATTERN, type Company } from "@/domains/companies/types";

interface CompanyCreateDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (c: Company) => void;
}

export function CompanyCreateDialog({ open, onClose, onCreated }: CompanyCreateDialogProps) {
  const [companyId, setCompanyId] = useState("");
  const [name, setName] = useState("");
  const create = useCreateCompany();

  useEffect(() => {
    if (open) {
      setCompanyId("");
      setName("");
      create.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const idValid = COMPANY_ID_PATTERN.test(companyId);
  const canSubmit = idValid && name.trim().length > 0 && !create.isPending;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    create.mutate({ company_id: companyId, name: name.trim() }, { onSuccess: onCreated });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="회사 생성"
      locked={create.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={create.isPending}>
            취소
          </Button>
          <Button type="submit" form="company-create-form" loading={create.isPending} disabled={!canSubmit}>
            생성
          </Button>
        </>
      }
    >
      <form id="company-create-form" onSubmit={onSubmit} className="space-y-4">
        <Field
          label="회사 ID"
          htmlFor="company-id"
          hint="소문자·숫자·하이픈, 2~63자, 첫 글자는 소문자 또는 숫자. 생성 후 변경할 수 없습니다."
        >
          <Input
            id="company-id"
            value={companyId}
            onChange={(e) => setCompanyId(e.target.value.trim())}
            placeholder="acme-corp"
            autoFocus
            aria-invalid={companyId.length > 0 && !idValid}
            className={companyId.length > 0 && !idValid ? "border-destructive" : undefined}
          />
        </Field>
        <Field label="회사 이름" htmlFor="company-name">
          <Input id="company-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Acme Corp" />
        </Field>
        <ErrorAlert error={create.error} />
      </form>
    </Dialog>
  );
}
