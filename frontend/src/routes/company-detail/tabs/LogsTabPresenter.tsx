import { RefreshCw, RotateCcw, Search } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { LogsTable } from "@/components/shared/LogsTable";
import type { LogRecord } from "@/domains/logs/types";

interface LogsTabPresenterProps {
  logs: LogRecord[];
  startLocal: string;
  endLocal: string;
  onStartChange: (v: string) => void;
  onEndChange: (v: string) => void;
  onApply: () => void;
  onReset: () => void;
  onRefresh: () => void;
  isLoading: boolean;
  isRefreshing: boolean;
  error: unknown;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
}

export function LogsTabPresenter(p: LogsTabPresenterProps) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor="log-start">시작</Label>
          <Input id="log-start" type="datetime-local" value={p.startLocal} onChange={(e) => p.onStartChange(e.target.value)} className="w-56" />
        </div>
        <div className="space-y-1">
          <Label htmlFor="log-end">종료</Label>
          <Input id="log-end" type="datetime-local" value={p.endLocal} onChange={(e) => p.onEndChange(e.target.value)} className="w-56" />
        </div>
        <Button onClick={p.onApply}>
          <Search className="h-4 w-4" /> 조회
        </Button>
        <Button variant="ghost" size="sm" onClick={p.onReset}>
          <RotateCcw className="h-3.5 w-3.5" /> 초기화
        </Button>
        <Button variant="outline" size="sm" onClick={p.onRefresh} className="ml-auto" loading={p.isRefreshing}>
          <RefreshCw className="h-3.5 w-3.5" /> 새로고침
        </Button>
      </div>
      <ErrorAlert error={p.error} />
      <LogsTable
        logs={p.logs}
        isLoading={p.isLoading}
        isRefreshing={p.isRefreshing}
        hasNextPage={p.hasNextPage}
        isFetchingNextPage={p.isFetchingNextPage}
        onLoadMore={p.onLoadMore}
      />
    </div>
  );
}
