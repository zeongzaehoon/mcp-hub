import { useMemo, useState } from "react";
import { useReconcile, useResolveKey } from "@/domains/ops/mutations";
import { useGlobalStats, useHealth, useRecentEvents, useRedisPeek } from "@/domains/ops/queries";
import type { RedisKind } from "@/domains/ops/types";
import { OpsPresenter } from "./OpsPresenter";

export function OpsContainer() {
  const [rawKey, setRawKey] = useState("");
  const resolve = useResolveKey();
  const reconcile = useReconcile();

  const [peekKind, setPeekKind] = useState<RedisKind>("company");
  const [peekIdent, setPeekIdent] = useState("");
  const [peekSubmitted, setPeekSubmitted] = useState<{ kind: RedisKind; ident: string } | null>(null);
  const peek = useRedisPeek(peekSubmitted?.kind ?? "company", peekSubmitted?.ident ?? "", peekSubmitted !== null);

  const stats = useGlobalStats();
  const health = useHealth();
  const events = useRecentEvents(50);
  // rows have no id of their own; index keeps DataTable's row ids unique
  const eventRows = useMemo(() => (events.data ?? []).map((e, i) => ({ ...e, _i: i })), [events.data]);

  return (
    <OpsPresenter
      stats={stats.data}
      statsError={stats.error}
      health={health.data}
      healthError={health.error}
      events={eventRows}
      eventsError={events.error}
      eventsLoading={events.isPending}
      eventsRefreshing={events.isFetching}
      rawKey={rawKey}
      onRawKeyChange={setRawKey}
      onResolve={() => {
        if (rawKey.trim()) resolve.mutate({ raw_key: rawKey.trim() });
      }}
      resolvePending={resolve.isPending}
      resolveResult={resolve.data}
      resolveError={resolve.error}
      onReconcile={() => reconcile.mutate()}
      reconcilePending={reconcile.isPending}
      reconcileResult={reconcile.data}
      reconcileError={reconcile.error}
      peekKind={peekKind}
      onPeekKindChange={setPeekKind}
      peekIdent={peekIdent}
      onPeekIdentChange={setPeekIdent}
      onPeek={() => {
        const ident = peekIdent.trim();
        if (ident) setPeekSubmitted({ kind: peekKind, ident });
      }}
      onPeekRefresh={() => void peek.refetch()}
      peekPending={peek.isFetching}
      peekResult={peekSubmitted ? peek.data : undefined}
      peekError={peekSubmitted ? peek.error : null}
    />
  );
}
