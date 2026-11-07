import { useMemo, useState } from "react";
import { flattenPages } from "@/lib/cursor";
import { fromDatetimeLocal } from "@/lib/utils";
import { useCompanyLogsInfinite } from "@/domains/logs/queries";
import type { LogFilters } from "@/domains/logs/types";
import { LogsTabPresenter } from "./LogsTabPresenter";

export function LogsTabContainer({ companyId }: { companyId: string }) {
  const [startLocal, setStartLocal] = useState("");
  const [endLocal, setEndLocal] = useState("");
  const [applied, setApplied] = useState<LogFilters>({});

  const query = useCompanyLogsInfinite(companyId, applied);
  const logs = useMemo(() => flattenPages(query.data), [query.data]);

  const apply = () => {
    setApplied({
      start: fromDatetimeLocal(startLocal) ?? undefined,
      end: fromDatetimeLocal(endLocal) ?? undefined,
    });
  };

  const reset = () => {
    setStartLocal("");
    setEndLocal("");
    setApplied({});
  };

  return (
    <LogsTabPresenter
      logs={logs}
      startLocal={startLocal}
      endLocal={endLocal}
      onStartChange={setStartLocal}
      onEndChange={setEndLocal}
      onApply={apply}
      onReset={reset}
      onRefresh={() => void query.refetch()}
      isLoading={query.isPending}
      isRefreshing={query.isFetching && !query.isFetchingNextPage}
      error={query.error}
      hasNextPage={query.hasNextPage}
      isFetchingNextPage={query.isFetchingNextPage}
      onLoadMore={() => void query.fetchNextPage()}
    />
  );
}
