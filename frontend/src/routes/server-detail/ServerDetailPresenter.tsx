import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Code } from "@/components/ui/Code";
import { PageSpinner } from "@/components/ui/Spinner";
import { Tabs } from "@/components/ui/Tabs";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { PageHeader } from "@/components/shared/PageHeader";
import { StatusBadge } from "@/components/shared/StatusBadge";
import type { Server } from "@/domains/servers/types";
import { ServerOverviewTabContainer } from "./tabs/ServerOverviewTabContainer";
import { ServerBindingsTab } from "./tabs/ServerBindingsTab";
import { ServerAccessTab } from "./tabs/ServerAccessTab";
import { ServerToolsTab } from "./tabs/ServerToolsTab";

export type ServerTab = "overview" | "bindings" | "access" | "tools";

export interface ServerTabDef {
  key: ServerTab;
  label: string;
  masterOnly?: boolean;
}

export const SERVER_TABS: readonly ServerTabDef[] = [
  { key: "overview", label: "개요" },
  { key: "tools", label: "도구 카탈로그" },
  { key: "bindings", label: "바인딩", masterOnly: true },
  { key: "access", label: "사용자 접근", masterOnly: true },
];

interface ServerDetailPresenterProps {
  serverId: string;
  server: Server | undefined;
  isLoading: boolean;
  error: unknown;
  tab: ServerTab;
  onTabChange: (tab: ServerTab) => void;
  isMaster: boolean;
}

export function ServerDetailPresenter({ serverId, server, isLoading, error, tab, onTabChange, isMaster }: ServerDetailPresenterProps) {
  const tabs = SERVER_TABS.filter((t) => !t.masterOnly || isMaster).map((t) => ({ key: t.key, label: t.label }));
  return (
    <div>
      <PageHeader
        breadcrumb={
          <Link to="/servers" className="inline-flex items-center gap-1 hover:text-foreground">
            <ArrowLeft className="h-3 w-3" /> 서버 목록
          </Link>
        }
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            {server?.name ?? serverId}
            {server ? <Badge variant="outline">{server.server_type}</Badge> : null}
            {server ? <StatusBadge status={server.status} /> : null}
            <Code className="font-normal">{serverId}</Code>
            {server?.endpoint ? <span className="font-mono text-xs font-normal text-muted-foreground">{server.endpoint}</span> : null}
          </span>
        }
      />
      <ErrorAlert error={error} className="mb-4" />
      {isLoading && !server ? (
        <PageSpinner />
      ) : server ? (
        <>
          <Tabs items={tabs} value={tab} onChange={onTabChange} className="mb-4" />
          {tab === "overview" ? <ServerOverviewTabContainer server={server} isMaster={isMaster} /> : null}
          {tab === "tools" ? <ServerToolsTab server={server} /> : null}
          {tab === "bindings" && isMaster ? <ServerBindingsTab serverId={serverId} /> : null}
          {tab === "access" && isMaster ? <ServerAccessTab serverId={serverId} /> : null}
        </>
      ) : null}
    </div>
  );
}
