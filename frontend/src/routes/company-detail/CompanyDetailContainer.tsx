import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { useIsMaster } from "@/stores/auth";
import { useCompany } from "@/domains/companies/queries";
import { CompanyDetailPresenter, type CompanyTab, COMPANY_TABS } from "./CompanyDetailPresenter";

function parseTab(raw: string | null): CompanyTab {
  return COMPANY_TABS.some((t) => t.key === raw) ? (raw as CompanyTab) : "overview";
}

export function CompanyDetailContainer({ companyId }: { companyId: string }) {
  const isMaster = useIsMaster();
  const query = useCompany(companyId);
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = parseTab(searchParams.get("tab"));
  const accessUserId = searchParams.get("user") ?? "";

  const onTabChange = useCallback(
    (next: CompanyTab) => {
      setSearchParams((prev) => {
        const sp = new URLSearchParams(prev);
        if (next === "overview") sp.delete("tab");
        else sp.set("tab", next);
        sp.delete("user");
        return sp;
      });
    },
    [setSearchParams],
  );

  /** Users tab "서버 접근" → access tab filtered to that user. */
  const onOpenUserAccess = useCallback(
    (userId: string) => {
      setSearchParams((prev) => {
        const sp = new URLSearchParams(prev);
        sp.set("tab", "access");
        sp.set("user", userId);
        return sp;
      });
    },
    [setSearchParams],
  );

  const onAccessUserFilterChange = useCallback(
    (userId: string) => {
      setSearchParams(
        (prev) => {
          const sp = new URLSearchParams(prev);
          if (userId) sp.set("user", userId);
          else sp.delete("user");
          return sp;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  return (
    <CompanyDetailPresenter
      companyId={companyId}
      company={query.data}
      isLoading={query.isPending}
      error={query.error}
      tab={tab}
      onTabChange={onTabChange}
      isMaster={isMaster}
      accessUserId={accessUserId}
      onOpenUserAccess={onOpenUserAccess}
      onAccessUserFilterChange={onAccessUserFilterChange}
    />
  );
}
