import { Save } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Code } from "@/components/ui/Code";
import { Input } from "@/components/ui/Input";
import { Field } from "@/components/ui/Label";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { HealthBadge } from "@/components/shared/HealthBadge";
import { KeyValueList } from "@/components/shared/KeyValueList";
import { Sparkline } from "@/components/shared/Sparkline";
import { StatusActions, TRANSITION_LABEL, type StatusTransition } from "@/components/shared/StatusActions";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { formatDateTime } from "@/lib/utils";
import { ALLOWED_TENANT_MODES } from "@/domains/common/enums";
import { DSN_PLACEHOLDER, type Server } from "@/domains/servers/types";
import type { MetricsOut } from "@/domains/ops/types";

export interface ServerEditForm {
  name: string;
  description: string;
  endpoint: string;
  /** new DSN to store; empty keeps the current connection */
  dsn: string;
}

interface ServerOverviewTabPresenterProps {
  server: Server;
  isMaster: boolean;
  metrics: MetricsOut | undefined;
  metricsError: unknown;
  form: ServerEditForm;
  onFormChange: (patch: Partial<ServerEditForm>) => void;
  onClearConnection: () => void;
  onSave: () => void;
  dirty: boolean;
  isSaving: boolean;
  error: unknown;
  pendingTransition: StatusTransition | null;
  onRequestTransition: (t: StatusTransition) => void;
  onCancelTransition: () => void;
  onConfirmTransition: () => void;
}

const ms = (v: number | null | undefined) => (v === null || v === undefined ? "-" : `${Math.round(v)}ms`);

export function ServerOverviewTabPresenter(p: ServerOverviewTabPresenterProps) {
  const { server } = p;
  const deleted = server.status === "deleted";
  const canEdit = p.isMaster && !deleted;
  const m = p.metrics;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card data-testid="server-health-card">
        <CardHeader>
          <CardTitle>상태</CardTitle>
          <HealthBadge health={server.health} showLatency={false} />
        </CardHeader>
        <CardContent>
          {server.health ? (
            <KeyValueList
              items={[
                { label: "응답 시간", value: ms(server.health.latency_ms) },
                { label: "확인 시각", value: formatDateTime(server.health.checked_at) },
                ...(server.health.error ? [{ label: "오류", value: <span className="font-mono text-xs text-destructive">{server.health.error}</span> }] : []),
              ]}
            />
          ) : (
            <p className="text-sm text-muted-foreground">이 서버 타입의 헬스 체크가 설정되어 있지 않습니다 (MCP_INTERNAL_*).</p>
          )}
        </CardContent>
      </Card>

      <Card data-testid="server-metrics-card">
        <CardHeader>
          <CardTitle>최근 24시간</CardTitle>
          {m ? <span className="font-mono text-xs text-muted-foreground">{m.calls.toLocaleString()} calls</span> : null}
        </CardHeader>
        <CardContent className="space-y-3">
          <ErrorAlert error={p.metricsError} />
          {m ? (
            <>
              <Sparkline series={m.series} />
              <KeyValueList
                items={[
                  { label: "호출", value: m.calls.toLocaleString() },
                  { label: "거부", value: `${m.denied.toLocaleString()} (${(m.denied_rate * 100).toFixed(1)}%)` },
                  { label: "오류", value: m.errors.toLocaleString() },
                  { label: "p50", value: ms(m.p50_ms) },
                  { label: "p99", value: ms(m.p99_ms) },
                  { label: "최대", value: ms(m.max_ms) },
                ]}
              />
            </>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>기본 정보</CardTitle>
        </CardHeader>
        <CardContent>
          <KeyValueList
            items={[
              { label: "서버 ID", value: <Code>{server.server_id}</Code> },
              { label: "타입", value: <Badge variant="outline">{server.server_type}</Badge> },
              { label: "상태", value: <StatusBadge status={server.status} /> },
              { label: "버전", value: server.version },
              {
                label: "허용 테넌트 격리 방식",
                value: (
                  <span className="inline-flex gap-1">
                    {ALLOWED_TENANT_MODES[server.server_type].map((m) => (
                      <Badge key={m} variant="secondary">
                        {m}
                      </Badge>
                    ))}
                  </span>
                ),
              },
              { label: "도구 수", value: `${Object.keys(server.tools).length}개` },
              { label: "생성일", value: formatDateTime(server.created_at) },
              { label: "수정일", value: formatDateTime(server.updated_at) },
            ]}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>설정</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label="이름" htmlFor="server-edit-name">
            <Input id="server-edit-name" value={p.form.name} onChange={(e) => p.onFormChange({ name: e.target.value })} disabled={!canEdit} />
          </Field>
          <Field label="설명" htmlFor="server-edit-desc">
            <Input id="server-edit-desc" value={p.form.description} onChange={(e) => p.onFormChange({ description: e.target.value })} disabled={!canEdit} />
          </Field>
          <Field label="엔드포인트" htmlFor="server-edit-endpoint">
            <Input id="server-edit-endpoint" value={p.form.endpoint} onChange={(e) => p.onFormChange({ endpoint: e.target.value })} disabled={!canEdit} />
          </Field>
          <Field
            label="데이터소스 연결"
            htmlFor="server-edit-dsn"
            hint={p.server.has_connection ? `현재: ${p.server.connection_hint ?? "(저장됨)"} — 새 값을 입력하면 교체됩니다` : "미설정: MCP 프로세스의 기본 연결(env) 사용. DSN을 입력해 저장하면 이 서버만의 데이터소스로 연결됩니다."}
          >
            <div className="flex items-center gap-2">
              <Input id="server-edit-dsn" type="password" value={p.form.dsn} onChange={(e) => p.onFormChange({ dsn: e.target.value })} disabled={!canEdit} placeholder={DSN_PLACEHOLDER[p.server.server_type]} autoComplete="off" />
              {canEdit && p.server.has_connection ? (
                <Button variant="ghost" size="sm" onClick={p.onClearConnection} disabled={p.isSaving}>
                  연결 제거
                </Button>
              ) : null}
            </div>
          </Field>
          {canEdit ? (
            <Button onClick={p.onSave} disabled={!p.dirty} loading={p.isSaving && !p.pendingTransition}>
              <Save className="h-4 w-4" /> 저장
            </Button>
          ) : (
            <p className="text-xs text-muted-foreground">마스터만 수정할 수 있습니다</p>
          )}

          <div className="space-y-1.5 border-t border-border pt-4">
            <div className="text-sm font-medium">상태 변경</div>
            {p.isMaster ? (
              <StatusActions status={server.status} onAction={p.onRequestTransition} disabled={p.isSaving} />
            ) : (
              <p className="text-xs text-muted-foreground">마스터만 상태를 변경할 수 있습니다</p>
            )}
            {!deleted ? (
              <p className="text-xs text-muted-foreground">서버를 일시중지하면 이 서버를 대상으로 하는 모든 회사의 API 키가 즉시 거부됩니다.</p>
            ) : null}
          </div>
          {!p.pendingTransition ? <ErrorAlert error={p.error} /> : null}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={p.pendingTransition !== null}
        title={p.pendingTransition ? `서버 ${TRANSITION_LABEL[p.pendingTransition]}` : ""}
        description={
          <span>
            <strong>{server.name}</strong> 서버를 {p.pendingTransition ? TRANSITION_LABEL[p.pendingTransition] : ""}합니다.
            {p.pendingTransition === "delete" ? " 삭제는 되돌릴 수 없습니다." : ""}
          </span>
        }
        confirmLabel={p.pendingTransition ? TRANSITION_LABEL[p.pendingTransition] : "확인"}
        destructive={p.pendingTransition === "delete"}
        loading={p.isSaving}
        error={p.error}
        onConfirm={p.onConfirmTransition}
        onClose={p.onCancelTransition}
      />
    </div>
  );
}
