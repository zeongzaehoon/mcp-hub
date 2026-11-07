import { useEffect, useState } from "react";
import { useDeleteBranding, usePatchCompany } from "@/domains/companies/mutations";
import { useCompanyStats } from "@/domains/companies/queries";
import type { Company } from "@/domains/companies/types";
import { useAuthStore } from "@/stores/auth";
import { TRANSITION_TARGET, type StatusTransition } from "@/components/shared/StatusActions";
import type { CompanyTab } from "../CompanyDetailPresenter";
import { OverviewTabPresenter } from "./OverviewTabPresenter";

interface OverviewTabContainerProps {
  company: Company;
  isMaster: boolean;
  /** onboarding checklist jumps to other tabs */
  onTabChange: (tab: CompanyTab) => void;
}

export function OverviewTabContainer({ company, isMaster, onTabChange }: OverviewTabContainerProps) {
  const [name, setName] = useState(company.name);
  const [pendingTransition, setPendingTransition] = useState<StatusTransition | null>(null);
  const patch = usePatchCompany(company.company_id);
  const stats = useCompanyStats(company.company_id);
  const [brandingOpen, setBrandingOpen] = useState(false);
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const resetBranding = useDeleteBranding(company.company_id);
  // backend: master, or an admin of this company (require_company_access)
  const isCompanyAdmin = useAuthStore((s) => s.user?.role === "admin" && s.user.company_id === company.company_id);
  const deleted = company.status === "deleted";
  const canEditBranding = !deleted && (isMaster || isCompanyAdmin);

  useEffect(() => {
    setName(company.name);
  }, [company.name]);

  const saveName = () => {
    const trimmed = name.trim();
    if (!trimmed || trimmed === company.name) return;
    patch.mutate({ name: trimmed });
  };

  const confirmTransition = () => {
    if (!pendingTransition) return;
    patch.mutate({ status: TRANSITION_TARGET[pendingTransition] }, { onSuccess: () => setPendingTransition(null) });
  };

  return (
    <OverviewTabPresenter
      company={company}
      isMaster={isMaster}
      onTabChange={onTabChange}
      stats={stats.data}
      statsError={stats.error}
      name={name}
      onNameChange={setName}
      onSaveName={saveName}
      nameDirty={name.trim() !== company.name && name.trim().length > 0}
      isSaving={patch.isPending}
      error={patch.error}
      pendingTransition={pendingTransition}
      onRequestTransition={setPendingTransition}
      onCancelTransition={() => {
        setPendingTransition(null);
        patch.reset();
      }}
      onConfirmTransition={confirmTransition}
      canEditBranding={canEditBranding}
      brandingReadOnlyHint={deleted ? "삭제된 회사는 수정할 수 없습니다" : "마스터 또는 이 회사의 관리자만 수정할 수 있습니다"}
      brandingOpen={brandingOpen}
      onBrandingOpenChange={setBrandingOpen}
      resetConfirmOpen={resetConfirmOpen}
      onRequestResetBranding={() => {
        setBrandingOpen(false);
        resetBranding.reset();
        setResetConfirmOpen(true);
      }}
      onCancelResetBranding={() => {
        setResetConfirmOpen(false);
        resetBranding.reset();
      }}
      onConfirmResetBranding={() => resetBranding.mutate(undefined, { onSuccess: () => setResetConfirmOpen(false) })}
      isResettingBranding={resetBranding.isPending}
      resetBrandingError={resetBranding.error}
      canEditSso={canEditBranding}
      ssoReadOnlyHint={deleted ? "삭제된 회사는 수정할 수 없습니다" : "마스터 또는 이 회사의 관리자만 수정할 수 있습니다"}
    />
  );
}
