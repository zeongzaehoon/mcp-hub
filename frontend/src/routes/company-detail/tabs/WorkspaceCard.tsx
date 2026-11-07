import { useState } from "react";
import { ChevronDown, ChevronRight, KeyRound, RefreshCw, Unplug } from "lucide-react";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Code } from "@/components/ui/Code";
import { Spinner } from "@/components/ui/Spinner";
import { CopyButton } from "@/components/shared/CopyButton";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { KeyValueList, type KeyValueItem } from "@/components/shared/KeyValueList";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { formatDateTime } from "@/lib/utils";
import { DIRECTORY_SCOPES, type WorkspaceOut, type WorkspaceSyncOut } from "@/domains/workspace/types";
import { PolicyApplySummary } from "./PoliciesTabPresenter";

interface WorkspaceCardProps {
  workspace: WorkspaceOut | null | undefined;
  isLoading: boolean;
  loadError: unknown;
  canEdit: boolean;
  /** shown when editing is not allowed */
  readOnlyHint?: string;
  /** opens the service-account dialog (first connect / replace credentials) */
  onConnect: () => void;
  onSync: () => void;
  syncPending: boolean;
  syncResult: WorkspaceSyncOut | undefined;
  syncError: unknown;
  onRequestDisconnect: () => void;
}

/** "3분 전" style; absolute time goes next to it. */
function formatRelative(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(diff)) return "";
  const m = Math.round(diff / 60_000);
  if (m < 1) return "방금";
  if (m < 60) return `${m}분 전`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}시간 전`;
  return `${Math.round(h / 24)}일 전`;
}

const SKIPPED_PREVIEW = 3;

function syncItems(s: WorkspaceSyncOut): KeyValueItem[] {
  const skippedPreview = s.skipped.slice(0, SKIPPED_PREVIEW).join(", ");
  return [
    { label: "사용자 (확인/생성/수정)", value: `${s.users_seen} / ${s.users_created} / ${s.users_updated}` },
    { label: "사용자 (정지/재개)", value: `${s.users_suspended} / ${s.users_reactivated}` },
    { label: "팀 (확인/생성/수정/정지)", value: `${s.teams_seen} / ${s.teams_created} / ${s.teams_updated} / ${s.teams_suspended}` },
    { label: "멤버 (추가/제거)", value: `${s.members_added} / ${s.members_removed}` },
    {
      label: "건너뜀",
      value:
        s.skipped.length === 0 ? (
          <span className="text-muted-foreground">0</span>
        ) : (
          <span title={s.skipped.join("\n")}>
            {s.skipped.length}
            <span className="text-xs text-muted-foreground">
              {" "}
              ({skippedPreview}
              {s.skipped.length > SKIPPED_PREVIEW ? " …" : ""})
            </span>
          </span>
        ),
    },
  ];
}

/** Collapsible "how to set this up in Google Admin" — the scopes come from the API when connected. */
function AdminHelp({ scopes }: { scopes: string[] }) {
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className="rounded-[5px] border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-[13px] font-semibold text-muted-foreground hover:text-foreground"
      >
        <Chevron className="h-3.5 w-3.5" aria-hidden /> Google Admin 설정 방법
      </button>
      {open ? (
        <ol className="space-y-2 border-t border-border px-3 py-3 text-[13px] leading-relaxed [counter-reset:step]">
          <li className="flex gap-2">
            <span className="shrink-0 font-bold text-primary">1.</span>
            <span>Google Cloud 콘솔에서 서비스 계정을 만들고 JSON 키를 다운로드합니다 (IAM 및 관리자 → 서비스 계정 → 키 → JSON).</span>
          </li>
          <li className="flex gap-2">
            <span className="shrink-0 font-bold text-primary">2.</span>
            <span>
              Google Admin → 보안 → 액세스 및 데이터 관리 → API 제어 → 도메인 전체 위임 → <strong>새로 추가</strong>: 위의 클라이언트 ID와 아래 권한 범위
              3개를 등록합니다.
              {scopes.length ? (
                <ul className="mt-1 space-y-0.5">
                  {scopes.map((s) => (
                    <li key={s} className="font-mono text-xs text-muted-foreground">
                      {s}
                    </li>
                  ))}
                </ul>
              ) : null}
            </span>
          </li>
          <li className="flex gap-2">
            <span className="shrink-0 font-bold text-primary">3.</span>
            <span>여기서 JSON 키와 위임 대상 관리자 이메일을 등록하고 &apos;지금 동기화&apos;를 누릅니다.</span>
          </li>
        </ol>
      ) : null}
    </div>
  );
}

const none = <span className="text-muted-foreground">없음</span>;

/** mono value + icon copy button; legacy rows (pre service-account) may have empty values → "없음", nothing to copy */
function MonoCopy({ value, label, testId }: { value: string; label: string; testId?: string }) {
  if (!value) return <span data-testid={testId}>{none}</span>;
  return (
    <span className="inline-flex max-w-full items-center gap-1.5">
      <Code className="min-w-0 truncate" data-testid={testId} title={value}>
        {value}
      </Code>
      <CopyButton value={value} label={label} iconOnly />
    </span>
  );
}

export function WorkspaceCard(p: WorkspaceCardProps) {
  const { workspace: ws } = p;
  // legacy rows carry no scopes; the setup help still needs the list to authorise
  const scopes = ws && ws.scopes.length ? ws.scopes : [...DIRECTORY_SCOPES];

  return (
    <Card data-testid="workspace-card">
      <CardHeader>
        <CardTitle className="inline-flex items-center gap-2">
          Google Workspace 연동
          {ws?.needs_reconnect ? (
            <Badge variant="danger" dot="danger" data-testid="workspace-needs-reconnect">
              재연결 필요
            </Badge>
          ) : null}
        </CardTitle>
        {ws && p.canEdit ? (
          <Button variant="outline" size="sm" onClick={p.onConnect} disabled={p.syncPending} data-testid="workspace-reconnect-button">
            <KeyRound className="h-3.5 w-3.5" /> 자격증명 교체
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4">
        {p.isLoading ? <Spinner className="h-4 w-4" /> : null}
        <ErrorAlert error={p.loadError} />

        {ws === null ? (
          <>
            <p className="text-sm text-muted-foreground">미연동</p>
            <p className="text-[13px] leading-relaxed text-muted-foreground">
              Google Cloud 서비스 계정 키를 등록하면 Workspace 디렉터리의 사용자·그룹을 읽어 와 사용자와 팀으로 동기화합니다. 직원의 Google 로그인은
              필요 없고, Workspace 관리자가 도메인 전체 위임만 허용하면 됩니다.
            </p>
            {p.canEdit ? (
              <Button onClick={p.onConnect} data-testid="workspace-connect-button">
                <KeyRound className="h-4 w-4" /> 서비스 계정 등록
              </Button>
            ) : null}
            <AdminHelp scopes={[...DIRECTORY_SCOPES]} />
          </>
        ) : null}

        {ws ? (
          <>
            <KeyValueList
              items={[
                { label: "상태", value: <StatusBadge status={ws.status} /> },
                { label: "도메인", value: <Code>{ws.domain}</Code> },
                { label: "위임 관리자", value: ws.admin_email || none },
                { label: "서비스 계정", value: ws.client_email ? <Code>{ws.client_email}</Code> : none },
                {
                  label: "클라이언트 ID",
                  value: <MonoCopy value={ws.client_id} label="클라이언트 ID 복사" testId="workspace-client-id" />,
                },
                {
                  label: "마지막 동기화",
                  value: ws.last_sync_at ? (
                    <span>
                      {formatRelative(ws.last_sync_at)}{" "}
                      <span className="text-xs text-muted-foreground">({formatDateTime(ws.last_sync_at)})</span>
                    </span>
                  ) : (
                    <span className="text-muted-foreground">아직 없음</span>
                  ),
                },
                { label: "등록한 운영자", value: ws.connected_by },
                {
                  label: "자격증명",
                  value: ws.has_credentials ? (
                    <span className="inline-flex items-center gap-1 text-sm">
                      <KeyRound className="h-3.5 w-3.5 text-muted-foreground" aria-hidden /> 저장됨
                    </span>
                  ) : (
                    <span className="text-destructive">없음</span>
                  ),
                },
              ]}
            />

            <div className="space-y-1.5">
              <div className="text-[13px] font-semibold text-muted-foreground">권한 범위 (Google Admin 도메인 전체 위임에 허용)</div>
              <ul className="space-y-1">
                {scopes.map((s) => (
                  <li key={s}>
                    <MonoCopy value={s} label={`권한 범위 복사: ${s}`} />
                  </li>
                ))}
              </ul>
            </div>

            {ws.needs_reconnect ? (
              <Alert variant="warning">
                {ws.has_credentials
                  ? "Google 이 이 서비스 계정의 위임을 거부했습니다. Google Admin 의 도메인 전체 위임에 위 클라이언트 ID 와 권한 범위 3개가 허용돼야 하며, 키가 삭제됐을 수 있습니다. 설정을 고친 뒤 '지금 동기화' 를 다시 누르거나, 새 키로 '자격증명 교체' 를 하세요."
                  : "저장된 서비스 계정 키가 없습니다. '자격증명 교체' 로 새 JSON 키를 등록하세요."}
              </Alert>
            ) : null}
            {ws.sync_error ? (
              <Alert variant="error" data-testid="workspace-sync-error">
                마지막 동기화 실패: {ws.sync_error}
              </Alert>
            ) : null}

            {ws.last_sync ? (
              <div className="space-y-1.5">
                <div className="text-[13px] font-semibold text-muted-foreground">마지막 동기화 결과</div>
                <KeyValueList items={syncItems(ws.last_sync)} />
              </div>
            ) : null}

            <AdminHelp scopes={scopes} />

            {p.canEdit ? (
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={p.onSync} loading={p.syncPending} data-testid="workspace-sync-button">
                  <RefreshCw className="h-3.5 w-3.5" /> 지금 동기화
                </Button>
                <Button variant="ghost" size="sm" className="text-destructive hover:bg-destructive-soft" onClick={p.onRequestDisconnect} disabled={p.syncPending}>
                  <Unplug className="h-3.5 w-3.5" /> 연결 해제
                </Button>
              </div>
            ) : null}

            {p.syncResult ? (
              <Alert variant="success" data-testid="workspace-sync-result">
                <div className="space-y-2">
                  <div>동기화 완료</div>
                  <KeyValueList items={syncItems(p.syncResult)} />
                  {p.syncResult.policies ? (
                    <>
                      <div className="text-xs font-medium">팀 정책 적용</div>
                      <PolicyApplySummary result={p.syncResult.policies} />
                    </>
                  ) : null}
                </div>
              </Alert>
            ) : null}
            <ErrorAlert error={p.syncError} />
          </>
        ) : null}

        {!p.canEdit && p.readOnlyHint ? <p className="text-xs text-muted-foreground">{p.readOnlyHint}</p> : null}
      </CardContent>
    </Card>
  );
}
