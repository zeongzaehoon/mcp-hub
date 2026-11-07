import { Save } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Code } from "@/components/ui/Code";
import { Input } from "@/components/ui/Input";
import { Field } from "@/components/ui/Label";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { KeyValueList } from "@/components/shared/KeyValueList";
import { StatusActions, TRANSITION_LABEL, type StatusTransition } from "@/components/shared/StatusActions";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { formatDateTime } from "@/lib/utils";
import type { Company, CompanyStats } from "@/domains/companies/types";
import { BrandingCard } from "./BrandingCard";
import { BrandingFormDialog } from "./BrandingFormDialog";
import { CompanyStatTiles } from "./CompanyStatTiles";
import type { CompanyTab } from "../CompanyDetailPresenter";
import { OnboardingChecklist } from "./OnboardingChecklist";
import { SsoCardContainer } from "./SsoCardContainer";
import { WorkspaceCardContainer } from "./WorkspaceCardContainer";

interface OverviewTabPresenterProps {
  company: Company;
  isMaster: boolean;
  onTabChange: (tab: CompanyTab) => void;
  stats: CompanyStats | undefined;
  statsError: unknown;
  name: string;
  onNameChange: (v: string) => void;
  onSaveName: () => void;
  nameDirty: boolean;
  isSaving: boolean;
  error: unknown;
  pendingTransition: StatusTransition | null;
  onRequestTransition: (t: StatusTransition) => void;
  onCancelTransition: () => void;
  onConfirmTransition: () => void;
  canEditBranding: boolean;
  brandingReadOnlyHint: string;
  brandingOpen: boolean;
  onBrandingOpenChange: (open: boolean) => void;
  resetConfirmOpen: boolean;
  onRequestResetBranding: () => void;
  onCancelResetBranding: () => void;
  onConfirmResetBranding: () => void;
  isResettingBranding: boolean;
  resetBrandingError: unknown;
  /** same rule as branding: master or admin of this company, not for deleted companies */
  canEditSso: boolean;
  ssoReadOnlyHint: string;
}

export function OverviewTabPresenter(p: OverviewTabPresenterProps) {
  const { company } = p;
  const deleted = company.status === "deleted";
  const canEdit = p.isMaster && !deleted;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <CompanyStatTiles stats={p.stats} error={p.statsError} />
      {!deleted ? <OnboardingChecklist companyId={company.company_id} onTabChange={p.onTabChange} /> : null}
      <Card>
        <CardHeader>
          <CardTitle>기본 정보</CardTitle>
        </CardHeader>
        <CardContent>
          <KeyValueList
            items={[
              { label: "회사 ID", value: <Code>{company.company_id}</Code> },
              { label: "상태", value: <StatusBadge status={company.status} /> },
              { label: "생성자", value: company.created_by },
              { label: "버전", value: company.version },
              { label: "생성일", value: formatDateTime(company.created_at) },
              { label: "수정일", value: formatDateTime(company.updated_at) },
            ]}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>설정</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <Field label="회사 이름" htmlFor="company-name-edit" hint={canEdit ? undefined : "마스터만 수정할 수 있습니다"}>
            <div className="flex gap-2">
              <Input
                id="company-name-edit"
                value={p.name}
                onChange={(e) => p.onNameChange(e.target.value)}
                disabled={!canEdit}
              />
              {canEdit ? (
                <Button onClick={p.onSaveName} disabled={!p.nameDirty} loading={p.isSaving && !p.pendingTransition}>
                  <Save className="h-4 w-4" /> 저장
                </Button>
              ) : null}
            </div>
          </Field>

          <div className="space-y-1.5">
            <div className="text-sm font-medium">상태 변경</div>
            {p.isMaster ? (
              <StatusActions status={company.status} onAction={p.onRequestTransition} disabled={p.isSaving} />
            ) : (
              <p className="text-xs text-muted-foreground">마스터만 상태를 변경할 수 있습니다</p>
            )}
            {!deleted ? (
              <p className="text-xs text-muted-foreground">
                일시중지하면 이 회사의 모든 API 키가 즉시 거부됩니다. 삭제는 되돌릴 수 없습니다.
              </p>
            ) : null}
          </div>
          {!p.pendingTransition ? <ErrorAlert error={p.error} /> : null}
        </CardContent>
      </Card>

      <BrandingCard
        branding={company.branding}
        canEdit={p.canEditBranding}
        readOnlyHint={p.brandingReadOnlyHint}
        onEdit={() => p.onBrandingOpenChange(true)}
      />
      <BrandingFormDialog
        company={company}
        open={p.brandingOpen}
        onClose={() => p.onBrandingOpenChange(false)}
        onRequestReset={p.onRequestResetBranding}
      />
      <ConfirmDialog
        open={p.resetConfirmOpen}
        title="기본 테마로 되돌리기"
        description={
          <span>
            <strong>{company.name}</strong>의 보고서 브랜딩(색상·로고·바닥글)을 삭제하고 기본(FTG) 테마로 되돌립니다.
          </span>
        }
        confirmLabel="되돌리기"
        destructive
        loading={p.isResettingBranding}
        error={p.resetBrandingError}
        onConfirm={p.onConfirmResetBranding}
        onClose={p.onCancelResetBranding}
      />

      <SsoCardContainer company={company} canEdit={p.canEditSso} readOnlyHint={p.ssoReadOnlyHint} />
      {/* same edit rule as SSO: master or admin of this company, not for deleted companies */}
      <WorkspaceCardContainer company={company} canEdit={p.canEditSso} readOnlyHint={p.ssoReadOnlyHint} />

      <ConfirmDialog
        open={p.pendingTransition !== null}
        title={p.pendingTransition ? `회사 ${TRANSITION_LABEL[p.pendingTransition]}` : ""}
        description={
          p.pendingTransition === "delete" ? (
            <span>
              <strong>{company.name}</strong> 회사를 삭제합니다. 삭제된 회사는 복구할 수 없으며 모든 키가 영구적으로 거부됩니다.
            </span>
          ) : (
            <span>
              <strong>{company.name}</strong> 회사를 {p.pendingTransition ? TRANSITION_LABEL[p.pendingTransition] : ""}합니다.
            </span>
          )
        }
        confirmLabel={p.pendingTransition ? TRANSITION_LABEL[p.pendingTransition] : "확인"}
        destructive={p.pendingTransition === "delete"}
        loading={p.isSaving}
        error={p.error}
        onConfirm={p.onConfirmTransition}
        onClose={p.onCancelTransition}
      />
    </div>
  );
}
