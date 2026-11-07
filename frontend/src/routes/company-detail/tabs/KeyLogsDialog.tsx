import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { LogsTable } from "@/components/shared/LogsTable";
import { flattenPages } from "@/lib/cursor";
import { fromDatetimeLocal } from "@/lib/utils";
import type { ApiKey } from "@/domains/apikeys/types";
import { useKeyLogsInfinite } from "@/domains/logs/queries";
import type { LogFilters } from "@/domains/logs/types";

interface KeyLogsDialogProps {
  companyId: string;
  apiKey: ApiKey | null;
  onClose: () => void;
}

export function KeyLogsDialog({ companyId, apiKey, onClose }: KeyLogsDialogProps) {
  const [startLocal, setStartLocal] = useState("");
  const [endLocal, setEndLocal] = useState("");
  const [applied, setApplied] = useState<LogFilters>({});
  const open = apiKey !== null;

  const query = useKeyLogsInfinite(companyId, apiKey?.key_id ?? "", applied, open);
  const logs = useMemo(() => flattenPages(query.data), [query.data]);

  const apply = () =>
    setApplied({ start: fromDatetimeLocal(startLocal) ?? undefined, end: fromDatetimeLocal(endLocal) ?? undefined });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={apiKey ? `접근 로그 — ${apiKey.name}` : "접근 로그"}
      description={apiKey ? `${apiKey.key_prefix}… · ${apiKey.key_id}` : undefined}
      size="xl"
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <Label htmlFor="key-log-start">시작</Label>
            <Input id="key-log-start" type="datetime-local" value={startLocal} onChange={(e) => setStartLocal(e.target.value)} className="w-56" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="key-log-end">종료</Label>
            <Input id="key-log-end" type="datetime-local" value={endLocal} onChange={(e) => setEndLocal(e.target.value)} className="w-56" />
          </div>
          <Button onClick={apply}>
            <Search className="h-4 w-4" /> 조회
          </Button>
        </div>
        <ErrorAlert error={query.error} />
        <LogsTable
          logs={logs}
          isLoading={query.isPending}
          isRefreshing={query.isFetching && !query.isFetchingNextPage}
          hasNextPage={query.hasNextPage}
          isFetchingNextPage={query.isFetchingNextPage}
          onLoadMore={() => void query.fetchNextPage()}
          height={420}
          showKeyId={false}
        />
      </div>
    </Dialog>
  );
}
