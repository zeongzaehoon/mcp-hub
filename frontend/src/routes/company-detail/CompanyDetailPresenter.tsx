import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Code } from "@/components/ui/Code";
import { PageSpinner } from "@/components/ui/Spinner";
import { Tabs, type TabItem } from "@/components/ui/Tabs";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatusBadge } from "@/components/shared/StatusBadge";
import type { Company } from "@/domains/companies/types";
import { OverviewTabContainer } from "./tabs/OverviewTabContainer";
import { UsersTabContainer } from "./tabs/UsersTabContainer";
import { TeamsTabContainer } from "./tabs/TeamsTabContainer";
import { BindingsTabContainer } from "./tabs/BindingsTabContainer";
import { AccessTabContainer } from "./tabs/AccessTabContainer";
import { PoliciesTabContainer } from "./tabs/PoliciesTabContainer";
import { ApiKeysTabContainer } from "./tabs/ApiKeysTabContainer";
import { LogsTabContainer } from "./tabs/LogsTabContainer";
import { ArtifactsTabContainer } from "./tabs/ArtifactsTabContainer";

export type CompanyTab = "overview" | "users" | "teams" | "bindings" | "access" | "policies" | "apikeys" | "logs" | "artifacts";

export const COMPANY_TABS: readonly TabItem<CompanyTab>[] = [
  { key: "overview", label: "개요" },
  { key: "users", label: "사용자" },
  { key: "teams", label: "팀" },
  { key: "bindings", label: "서버 바인딩" },
  { key: "access", label: "서버 접근" },
  { key: "policies", label: "정책" },
  { key: "apikeys", label: "API 키" },
  { key: "logs", label: "접근 로그" },
  { key: "artifacts", label: "산출물" },
];

interface CompanyDetailPresenterProps {
  companyId: string;
  company: Company | undefined;
  isLoading: boolean;
  error: unknown;
  tab: CompanyTab;
  onTabChange: (tab: CompanyTab) => void;
  isMaster: boolean;
  /** user filter for the access tab (from the URL) */
  accessUserId: string;
  onOpenUserAccess: (userId: string) => void;
  onAccessUserFilterChange: (userId: string) => void;
}

export function CompanyDetailPresenter({
  companyId,
  company,
  isLoading,
  error,
  tab,
  onTabChange,
  isMaster,
  accessUserId,
  onOpenUserAccess,
  onAccessUserFilterChange,
}: CompanyDetailPresenterProps) {
  return (
    <div>
      <PageHeader
        breadcrumb={
          <Link to="/companies" className="inline-flex items-center gap-1 hover:text-foreground">
            <ArrowLeft className="h-3 w-3" /> 회사 목록
          </Link>
        }
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            {company?.name ?? companyId}
            {company ? <StatusBadge status={company.status} /> : null}
            <Code className="font-normal">{companyId}</Code>
            {company ? <span className="font-mono text-xs font-normal text-muted-foreground">v{company.version}</span> : null}
          </span>
        }
      />
      <ErrorAlert error={error} className="mb-4" />
      {isLoading && !company ? (
        <PageSpinner />
      ) : company ? (
        <>
          <Tabs items={COMPANY_TABS} value={tab} onChange={onTabChange} className="mb-4" />
          {tab === "overview" ? <OverviewTabContainer company={company} isMaster={isMaster} onTabChange={onTabChange} /> : null}
          {tab === "users" ? <UsersTabContainer companyId={companyId} companyStatus={company.status} onOpenAccess={onOpenUserAccess} /> : null}
          {tab === "teams" ? <TeamsTabContainer companyId={companyId} /> : null}
          {tab === "bindings" ? <BindingsTabContainer companyId={companyId} companyStatus={company.status} isMaster={isMaster} /> : null}
          {tab === "access" ? (
            <AccessTabContainer
              companyId={companyId}
              companyStatus={company.status}
              initialUserId={accessUserId}
              onUserFilterChange={onAccessUserFilterChange}
            />
          ) : null}
          {tab === "policies" ? <PoliciesTabContainer companyId={companyId} companyStatus={company.status} isMaster={isMaster} /> : null}
          {tab === "apikeys" ? <ApiKeysTabContainer companyId={companyId} companyStatus={company.status} /> : null}
          {tab === "logs" ? <LogsTabContainer companyId={companyId} /> : null}
          {tab === "artifacts" ? <ArtifactsTabContainer companyId={companyId} /> : null}
        </>
      ) : null}
    </div>
  );
}
