import { useMemo } from "react";
import { Link } from "react-router-dom";
import type { ColumnDef } from "@tanstack/react-table";
import { Activity, Database, HeartPulse, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Code, Pre } from "@/components/ui/Code";
import { Input } from "@/components/ui/Input";
import { Field } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { DataTable } from "@/components/shared/DataTable";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { HealthBadge } from "@/components/shared/HealthBadge";
import { KeyValueList } from "@/components/shared/KeyValueList";
import { StatTile, StatTileRow } from "@/components/shared/StatTile";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { formatDateTime } from "@/lib/utils";
import {
  REDIS_KIND_HINT,
  REDIS_KIND_VALUES,
  type EventOut,
  type GlobalStatsOut,
  type HealthOut,
  type ReconcileOut,
  type RedisKind,
  type RedisPeekOut,
  type ResolveOut,
} from "@/domains/ops/types";

export type EventRow = EventOut & { _i: number };

interface OpsPresenterProps {
  stats: GlobalStatsOut | undefined;
  statsError: unknown;
  health: HealthOut[] | undefined;
  healthError: unknown;
  events: EventRow[];
  eventsError: unknown;
  eventsLoading: boolean;
  eventsRefreshing: boolean;
  rawKey: string;
  onRawKeyChange: (v: string) => void;
  onResolve: () => void;
  resolvePending: boolean;
  resolveResult: ResolveOut | undefined;
  resolveError: unknown;
  onReconcile: () => void;
  reconcilePending: boolean;
  reconcileResult: ReconcileOut | undefined;
  reconcileError: unknown;
  peekKind: RedisKind;
  onPeekKindChange: (k: RedisKind) => void;
  peekIdent: string;
  onPeekIdentChange: (v: string) => void;
  onPeek: () => void;
  onPeekRefresh: () => void;
  peekPending: boolean;
  peekResult: RedisPeekOut | undefined;
  peekError: unknown;
}

function EffectiveBadge({ effective }: { effective: string }) {
  if (effective === "active") return <Badge variant="success">active</Badge>;
  if (effective === "unknown") return <Badge variant="secondary">unknown</Badge>;
  return <Badge variant="danger">{effective}</Badge>;
}

function EventStatusBadge({ status }: { status: EventOut["status"] }) {
  if (status === "ok") return <Badge variant="success" dot="accent">성공</Badge>;
  if (status === "denied") return <Badge variant="warning" dot="hollow">거부</Badge>;
  if (status === "error") return <Badge variant="danger" dot="danger">오류</Badge>;
  return <span className="text-xs text-muted-foreground">-</span>;
}

function useEventColumns(): ColumnDef<EventRow>[] {
  return useMemo<ColumnDef<EventRow>[]>(
    () => [
      { accessorKey: "ts", header: "시각", size: 180, cell: ({ row }) => <span className="font-mono text-xs">{formatDateTime(row.original.ts)}</span> },
      {
        accessorKey: "company_id",
        header: "회사",
        size: 160,
        cell: ({ row }) =>
          row.original.company_id ? (
            <Link to={`/companies/${encodeURIComponent(row.original.company_id)}`} className="hover:underline" onClick={(e) => e.stopPropagation()}>
              <Code>{row.original.company_id}</Code>
            </Link>
          ) : (
            "-"
          ),
      },
      { accessorKey: "server_type", header: "서버 타입", size: 110, cell: ({ row }) => (row.original.server_type ? <Badge variant="outline">{row.original.server_type}</Badge> : "-") },
      { accessorKey: "tool", header: "도구", size: 160, cell: ({ row }) => (row.original.tool ? <Code>{row.original.tool}</Code> : "-") },
      { accessorKey: "status", header: "상태", size: 90, cell: ({ row }) => <EventStatusBadge status={row.original.status} /> },
      { accessorKey: "duration_ms", header: "ms", size: 80, cell: ({ row }) => <span className="font-mono text-xs">{row.original.duration_ms ?? "-"}</span> },
      {
        accessorKey: "error",
        header: "오류",
        enableSorting: false,
        cell: ({ row }) =>
          row.original.error ? (
            <span className="font-mono text-xs text-muted-foreground" title={row.original.error}>
              {row.original.error.length > 60 ? `${row.original.error.slice(0, 59)}…` : row.original.error}
            </span>
          ) : (
            "-"
          ),
      },
    ],
    [],
  );
}

export function OpsPresenter(p: OpsPresenterProps) {
  const r = p.resolveResult;
  const calls = p.stats?.calls_24h ?? null;
  const okCount = p.health?.filter((h) => h.ok === true).length ?? 0;
  const downCount = p.health?.filter((h) => h.ok === false).length ?? 0;
  const eventColumns = useEventColumns();
  return (
    <div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-4 lg:col-span-2">
          <StatTileRow className="md:grid-cols-3 xl:grid-cols-3 md:[&>*]:border-b-0">
            <StatTile label="24h 호출" value={calls ? calls.calls.toLocaleString() : p.stats ? "0" : "–"} sub={calls ? `성공 ${calls.ok.toLocaleString()} · 오류 ${calls.errors.toLocaleString()}` : null} />
            <StatTile
              label="24h 거부율"
              value={calls ? `${(calls.denied_rate * 100).toFixed(1)}%` : p.stats ? "0.0%" : "–"}
              dot={calls && calls.denied > 0 ? "hollow" : undefined}
              sub={calls ? `거부 ${calls.denied.toLocaleString()}` : null}
            />
            <StatTile
              label="정상 서비스"
              value={p.health ? `${okCount} / ${p.health.length}` : "–"}
              dot={p.health ? (downCount > 0 ? "danger" : p.health.length > 0 && okCount === p.health.length ? "accent" : "hollow") : undefined}
              sub={p.health && p.health.length === 0 ? "헬스 체크 대상 없음" : null}
            />
          </StatTileRow>
          <ErrorAlert error={p.statsError} />
        </div>

        <Card className="lg:col-span-2" data-testid="ops-health">
          <CardHeader>
            <CardTitle className="inline-flex items-center gap-2">
              <HeartPulse className="h-4 w-4" /> MCP 서비스 상태
            </CardTitle>
            <span className="text-xs text-muted-foreground">30초마다 갱신</span>
          </CardHeader>
          <CardContent className="space-y-3">
            <ErrorAlert error={p.healthError} />
            {p.health && p.health.length === 0 ? (
              <p className="text-sm text-muted-foreground">설정된 헬스 체크 대상이 없습니다 (env MCP_INTERNAL_*).</p>
            ) : (
              <ul className="divide-y divide-border">
                {(p.health ?? []).map((h) => (
                  <li key={h.kind} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                    <Code className="w-28 text-center">{h.kind}</Code>
                    <HealthBadge health={h} />
                    <span className="text-xs text-muted-foreground">확인 {formatDateTime(h.checked_at)}</span>
                    {h.error ? <span className="font-mono text-xs text-destructive">{h.error}</span> : null}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="inline-flex items-center gap-2">
              <ShieldCheck className="h-4 w-4" /> 키 판정 (Resolve)
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              MCP 서버가 내리는 것과 동일한 허용/거부 판정을 DB 기준으로 계산하고, Redis 프로젝션과 일치하는지 확인합니다. 입력한 키는 서버로 전송되며 화면에는 저장되지 않습니다.
            </p>
            <form
              className="flex flex-wrap items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                p.onResolve();
              }}
            >
              <div className="min-w-0 flex-1">
                <Field label="Raw API key" htmlFor="ops-raw-key">
                  <Input
                    id="ops-raw-key"
                    type="password"
                    autoComplete="off"
                    value={p.rawKey}
                    onChange={(e) => p.onRawKeyChange(e.target.value)}
                    placeholder="mck_..."
                    className="font-mono"
                  />
                </Field>
              </div>
              <Button type="submit" loading={p.resolvePending} disabled={!p.rawKey.trim()}>
                <Search className="h-4 w-4" /> 판정
              </Button>
            </form>
            <ErrorAlert error={p.resolveError} />
            {r ? (
              <div className="space-y-3 rounded-[5px] border border-border p-4">
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  <span className="inline-flex items-center gap-2">
                    판정: <EffectiveBadge effective={r.effective} />
                  </span>
                  <span className="inline-flex items-center gap-2">
                    Redis 동기화:{" "}
                    {r.redis_in_sync ? <Badge variant="success">in sync</Badge> : <Badge variant="danger">out of sync</Badge>}
                  </span>
                </div>
                {!r.redis_in_sync ? (
                  <Alert variant="warning">Redis 프로젝션이 DB와 다릅니다. 아래 "지금 동기화"를 실행한 뒤 다시 판정하세요.</Alert>
                ) : null}
                {r.key ? (
                  <KeyValueList
                    items={[
                      { label: "키 이름", value: r.key.name },
                      { label: "키 ID", value: <Code>{r.key.key_id}</Code> },
                      { label: "프리픽스", value: <Code>{r.key.key_prefix}</Code> },
                      {
                        label: "회사",
                        value: (
                          <Link to={`/companies/${encodeURIComponent(r.key.company_id)}?tab=apikeys`} className="text-primary hover:underline">
                            {r.key.company_id}
                          </Link>
                        ),
                      },
                      { label: "사용자 ID", value: <Code>{r.key.user_id}</Code> },
                      {
                        label: "서버 범위",
                        value: r.key.server_ids === null ? <Badge variant="info">전체 서버</Badge> : r.key.server_ids.join(", ") || "-",
                      },
                      { label: "키 상태", value: <StatusBadge status={r.key.status} /> },
                      { label: "만료", value: r.key.expires_at ? formatDateTime(r.key.expires_at) : "무기한" },
                      { label: "최근 사용", value: formatDateTime(r.key.last_used_at) },
                    ]}
                  />
                ) : (
                  <p className="text-sm text-muted-foreground">DB에 해당 키가 없습니다 (unknown).</p>
                )}
                {r.key ? (
                  <div className="space-y-1.5">
                    <div className="text-xs text-muted-foreground">서버별 판정 (권한·도구는 사용자의 서버 접근 기준)</div>
                    {Object.keys(r.servers).length === 0 ? (
                      <p className="text-sm text-muted-foreground">범위 내 서버가 없습니다 — 모든 서버 호출이 거부됩니다.</p>
                    ) : (
                      <ul className="space-y-1 text-sm">
                        {Object.entries(r.servers).map(([sid, verdict]) => (
                          <li key={sid} className="flex items-center gap-2">
                            <Link to={`/servers/${encodeURIComponent(sid)}`} className="text-primary hover:underline">
                              <Code>{sid}</Code>
                            </Link>
                            <EffectiveBadge effective={verdict} />
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ) : null}
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="inline-flex items-center gap-2">
              <RefreshCw className="h-4 w-4" /> 강제 동기화 (Reconcile)
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              아웃박스를 비우고 DynamoDB 기준으로 Redis 프로젝션을 전수 점검·복구합니다. 백그라운드 주기 작업과 동일하며, 안전하게 여러 번 실행할 수 있습니다.
            </p>
            <Button onClick={p.onReconcile} loading={p.reconcilePending}>
              <RefreshCw className="h-4 w-4" /> 지금 동기화
            </Button>
            <ErrorAlert error={p.reconcileError} />
            {p.reconcileResult ? (
              <div className="space-y-2">
                <KeyValueList
                  items={[
                    { label: "점검", value: p.reconcileResult.checked.toLocaleString() },
                    { label: "복구", value: p.reconcileResult.repaired.toLocaleString() },
                    { label: "잔여 키 제거", value: p.reconcileResult.removed_stray.toLocaleString() },
                    { label: "오류", value: p.reconcileResult.errors.length.toLocaleString() },
                  ]}
                />
                {p.reconcileResult.errors.length > 0 ? <Pre value={p.reconcileResult.errors} /> : <Alert variant="success">오류 없이 완료되었습니다.</Alert>}
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="inline-flex items-center gap-2">
              <Database className="h-4 w-4" /> Redis 조회 (Peek)
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                p.onPeek();
              }}
            >
              <div className="grid grid-cols-3 gap-2">
                <Field label="종류" htmlFor="ops-peek-kind">
                  <Select id="ops-peek-kind" value={p.peekKind} onChange={(e) => p.onPeekKindChange(e.target.value as RedisKind)}>
                    {REDIS_KIND_VALUES.map((k) => (
                      <option key={k} value={k}>
                        {k}
                      </option>
                    ))}
                  </Select>
                </Field>
                <div className="col-span-2">
                  <Field label="식별자" htmlFor="ops-peek-ident" hint={REDIS_KIND_HINT[p.peekKind]}>
                    <Input id="ops-peek-ident" value={p.peekIdent} onChange={(e) => p.onPeekIdentChange(e.target.value)} className="font-mono" />
                  </Field>
                </div>
              </div>
              <div className="flex gap-2">
                <Button type="submit" loading={p.peekPending} disabled={!p.peekIdent.trim()}>
                  <Search className="h-4 w-4" /> 조회
                </Button>
                {p.peekResult ? (
                  <Button variant="outline" onClick={p.onPeekRefresh} disabled={p.peekPending}>
                    새로고침
                  </Button>
                ) : null}
              </div>
            </form>
            <ErrorAlert error={p.peekError} />
            {p.peekResult ? (
              p.peekResult.error ? (
                <Alert variant="error">{p.peekResult.error}</Alert>
              ) : (
                <div className="space-y-2">
                  <div className="text-xs text-muted-foreground">
                    key: <Code>{p.peekResult.key}</Code>
                  </div>
                  {p.peekResult.value === null || p.peekResult.value === undefined ? (
                    <Alert variant="warning">Redis에 값이 없습니다 (nil).</Alert>
                  ) : (
                    <Pre value={p.peekResult.value} />
                  )}
                </div>
              )
            ) : null}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2" data-testid="ops-events">
          <CardHeader>
            <CardTitle className="inline-flex items-center gap-2">
              <Activity className="h-4 w-4" /> 이벤트 스트림
            </CardTitle>
            <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
              {p.eventsRefreshing ? <RefreshCw className="h-3 w-3 animate-spin" aria-hidden /> : null}
              10초마다 갱신
            </span>
          </CardHeader>
          <CardContent className="space-y-3">
            <ErrorAlert error={p.eventsError} />
            <DataTable
              data={p.events}
              columns={eventColumns}
              getRowId={(e) => String(e._i)}
              isLoading={p.eventsLoading}
              height={420}
              emptyMessage="최근 이벤트가 없습니다"
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
