import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { useIsMaster } from "@/stores/auth";
import { useServer } from "@/domains/servers/queries";
import { ServerDetailPresenter, SERVER_TABS, type ServerTab } from "./ServerDetailPresenter";

function parseTab(raw: string | null, isMaster: boolean): ServerTab {
  const found = SERVER_TABS.find((t) => t.key === raw);
  if (!found) return "overview";
  if (found.masterOnly && !isMaster) return "overview";
  return found.key;
}

export function ServerDetailContainer({ serverId }: { serverId: string }) {
  const isMaster = useIsMaster();
  const query = useServer(serverId);
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = parseTab(searchParams.get("tab"), isMaster);

  const onTabChange = useCallback(
    (next: ServerTab) => {
      setSearchParams((prev) => {
        const sp = new URLSearchParams(prev);
        if (next === "overview") sp.delete("tab");
        else sp.set("tab", next);
        return sp;
      });
    },
    [setSearchParams],
  );

  return (
    <ServerDetailPresenter
      serverId={serverId}
      server={query.data}
      isLoading={query.isPending}
      error={query.error}
      tab={tab}
      onTabChange={onTabChange}
      isMaster={isMaster}
    />
  );
}
