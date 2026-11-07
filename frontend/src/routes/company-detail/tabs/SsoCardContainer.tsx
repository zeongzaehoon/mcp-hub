import { useState } from "react";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { useSsoOptions } from "@/domains/auth/queries";
import type { Company } from "@/domains/companies/types";
import { useDeleteSso, usePutSso, useTestSso } from "@/domains/sso/mutations";
import { useSso } from "@/domains/sso/queries";
import { SsoCard } from "./SsoCard";
import { SsoFormDialog } from "./SsoFormDialog";

interface SsoCardContainerProps {
  company: Company;
  canEdit: boolean;
  readOnlyHint: string;
}

export function SsoCardContainer({ company, canEdit, readOnlyHint }: SsoCardContainerProps) {
  const companyId = company.company_id;
  const query = useSso(companyId);
  const sso = query.data;
  const ssoOptions = useSsoOptions();
  const [formOpen, setFormOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const test = useTestSso(companyId);
  const toggle = usePutSso(companyId);
  const remove = useDeleteSso(companyId);

  const toggleStatus = () => {
    if (!sso) return;
    // PUT without client_secret keeps the stored one; for a platform provider issuer/client_id are ignored
    toggle.mutate({
      platform: sso.platform,
      issuer: sso.issuer,
      client_id: sso.client_id,
      domains: sso.domains,
      hosted_domain: sso.hosted_domain,
      auto_provision: sso.auto_provision,
      status: sso.status === "active" ? "suspended" : "active",
    });
  };

  return (
    <>
      <SsoCard
        sso={sso}
        isLoading={query.isPending}
        loadError={query.error}
        canEdit={canEdit}
        readOnlyHint={readOnlyHint}
        onEdit={() => {
          test.reset();
          toggle.reset();
          setFormOpen(true);
        }}
        onTest={() => test.mutate()}
        testPending={test.isPending}
        testResult={test.data}
        testError={test.error}
        onToggleStatus={toggleStatus}
        statusPending={toggle.isPending}
        statusError={toggle.error}
        onRequestDelete={() => {
          remove.reset();
          setDeleteOpen(true);
        }}
      />
      <SsoFormDialog
        companyId={companyId}
        current={sso ?? null}
        googleEnabled={ssoOptions.data?.google === true}
        open={formOpen}
        onClose={() => setFormOpen(false)}
      />
      <ConfirmDialog
        open={deleteOpen}
        title="SSO 로그인 삭제"
        description={
          <span>
            <strong>{company.name}</strong>의 SSO 설정을 삭제합니다. 이후 이 회사 도메인의 SSO 로그인은 거부되며, 이미 연결된 사용자 계정은
            그대로 남습니다.
          </span>
        }
        confirmLabel="삭제"
        destructive
        loading={remove.isPending}
        error={remove.error}
        onConfirm={() => remove.mutate(undefined, { onSuccess: () => setDeleteOpen(false) })}
        onClose={() => {
          setDeleteOpen(false);
          remove.reset();
        }}
      />
    </>
  );
}
