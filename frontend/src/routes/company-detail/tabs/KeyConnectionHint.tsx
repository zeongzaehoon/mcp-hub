import { Spinner } from "@/components/ui/Spinner";
import { useUserAccessPage } from "@/domains/access/queries";
import type { ApiKey } from "@/domains/apikeys/types";
import { mcpConnectUrl, type Server } from "@/domains/servers/types";

interface KeyConnectionHintProps {
  companyId: string;
  apiKey: ApiKey;
  serversById: Map<string, Server>;
}

/** How an MCP client connects with this key: one URL per server in scope; `?server=` unless the scope is exactly one server. */
export function KeyConnectionHint({ companyId, apiKey, serversById }: KeyConnectionHintProps) {
  const scopeAll = apiKey.server_ids === null;
  const userAccess = useUserAccessPage(companyId, apiKey.user_id, scopeAll);
  const serverIds = scopeAll
    ? (userAccess.data?.items ?? []).filter((a) => a.status === "active").map((a) => a.server_id)
    : (apiKey.server_ids ?? []);
  const single = !scopeAll && serverIds.length === 1;

  return (
    <div className="space-y-1 text-sm" data-testid="key-connection-hint">
      <p className="font-medium">MCP 연결 방법</p>
      <p className="text-xs">
        헤더 <code className="font-mono">Authorization: Bearer &lt;위 키&gt;</code> 로 아래 URL에 연결합니다.
        {single ? "" : " 키 범위가 서버 1개가 아니므로 URL의 ?server= 로 대상 서버를 지정해야 합니다."}
      </p>
      {scopeAll && userAccess.isPending ? (
        <Spinner className="h-4 w-4" />
      ) : serverIds.length === 0 ? (
        <p className="text-xs">현재 이 사용자에게 활성 서버 접근이 없습니다. 접근이 부여되면 해당 서버의 엔드포인트에 ?server=&lt;server_id&gt; 를 붙여 사용합니다.</p>
      ) : (
        <ul className="space-y-1">
          {serverIds.map((sid) => {
            const s = serversById.get(sid);
            const endpoint = s?.endpoint ?? "";
            return (
              <li key={sid} className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-medium">{s?.name ?? sid}</span>
                {endpoint ? (
                  <code className="select-all break-all rounded border border-border-strong bg-muted px-1.5 py-0.5 font-mono">
                    {mcpConnectUrl(endpoint, sid, !single)}
                  </code>
                ) : (
                  <span className="text-muted-foreground">엔드포인트 미설정 (server={sid})</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {scopeAll ? <p className="text-xs text-muted-foreground">범위가 "전체 서버"이므로 나중에 부여되는 서버도 같은 방식으로 사용할 수 있습니다.</p> : null}
    </div>
  );
}
