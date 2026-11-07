import { KeyRound, Pause, Play, PlugZap, Settings2, Trash2 } from "lucide-react";
import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Code } from "@/components/ui/Code";
import { Spinner } from "@/components/ui/Spinner";
import { CopyButton } from "@/components/shared/CopyButton";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { KeyValueList } from "@/components/shared/KeyValueList";
import { StatusBadge } from "@/components/shared/StatusBadge";
import type { SsoProviderOut, SsoTestOut } from "@/domains/sso/types";

interface SsoCardProps {
  sso: SsoProviderOut | null | undefined;
  isLoading: boolean;
  loadError: unknown;
  canEdit: boolean;
  /** shown when editing is not allowed */
  readOnlyHint?: string;
  onEdit: () => void;
  onTest: () => void;
  testPending: boolean;
  testResult: SsoTestOut | undefined;
  testError: unknown;
  onToggleStatus: () => void;
  statusPending: boolean;
  statusError: unknown;
  onRequestDelete: () => void;
}

const none = <span className="text-muted-foreground">없음</span>;

export function SsoCard(p: SsoCardProps) {
  const { sso } = p;
  const busy = p.testPending || p.statusPending;

  return (
    <Card data-testid="sso-card">
      <CardHeader>
        <CardTitle>직원 로그인 공급자 (커넥터 SSO)</CardTitle>
        {p.canEdit && sso ? (
          <Button variant="outline" size="sm" onClick={p.onEdit} disabled={busy}>
            <Settings2 className="h-4 w-4" /> 편집
          </Button>
        ) : null}
        {p.canEdit && sso === null ? (
          <Button variant="outline" size="sm" onClick={p.onEdit}>
            <Settings2 className="h-4 w-4" /> 설정
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4">
        {p.isLoading ? <Spinner className="h-4 w-4" /> : null}
        <ErrorAlert error={p.loadError} />

        <p className="text-[13px] text-muted-foreground">
          직원이 Claude Desktop 등 MCP 클라이언트의 동의 화면에서 &apos;회사 계정으로 로그인&apos;할 때 쓰는 IdP입니다. 인증만 담당하며
          권한은 서버 접근에서 그대로 관리됩니다.
        </p>
        {sso === null ? <p className="text-sm text-muted-foreground">SSO 미설정</p> : null}

        {sso ? (
          <>
            <KeyValueList
              items={[
                { label: "상태", value: <StatusBadge status={sso.status} /> },
                {
                  label: "종류",
                  value: (
                    <Badge variant="outline" data-testid="sso-provider-type">
                      {sso.platform === "google" ? "플랫폼 Google" : "OIDC"}
                    </Badge>
                  ),
                },
                { label: "Issuer", value: <Code>{sso.issuer}</Code> },
                // platform providers use the platform's own client: nothing to show
                ...(sso.platform
                  ? []
                  : [
                      { label: "Client ID", value: <Code>{sso.client_id}</Code> },
                      {
                        label: "Client Secret",
                        value: sso.has_secret ? (
                          <span className="inline-flex items-center gap-1 text-sm">
                            <KeyRound className="h-3.5 w-3.5 text-muted-foreground" aria-hidden /> 저장됨
                          </span>
                        ) : (
                          <span className="text-destructive">없음</span>
                        ),
                      },
                    ]),
                {
                  label: "이메일 도메인",
                  value: (
                    <span className="inline-flex flex-wrap gap-1">
                      {sso.domains.map((d) => (
                        <Badge key={d} variant="secondary">
                          {d}
                        </Badge>
                      ))}
                    </span>
                  ),
                },
                { label: "Hosted domain (Google hd)", value: sso.hosted_domain ? <Code>{sso.hosted_domain}</Code> : none },
                { label: "자동 프로비저닝", value: sso.auto_provision ? "허용 (도메인이 맞는 신규 사용자를 멤버로 생성)" : "꺼짐 (등록된 사용자만)" },
              ]}
            />

            <div className="space-y-1.5">
              <div className="text-xs text-muted-foreground">리디렉션 URI</div>
              <div className="flex items-center gap-2">
                <code
                  className="min-w-0 flex-1 select-all break-all rounded border border-border bg-muted px-2 py-1.5 font-mono text-xs"
                  data-testid="sso-redirect-uri"
                >
                  {sso.redirect_uri}
                </code>
                <CopyButton value={sso.redirect_uri} label="리디렉션 URI 복사" />
              </div>
              <p className="text-xs text-muted-foreground">
                {sso.platform === "google"
                  ? "플랫폼 Google 클라이언트에 이미 등록된 URI입니다. 회사가 따로 등록할 필요가 없습니다."
                  : "IdP(예: Google Cloud Console)의 승인된 리디렉션 URI에 이 값을 등록하세요."}
              </p>
            </div>

            {p.canEdit ? (
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" onClick={p.onTest} loading={p.testPending} disabled={p.statusPending} data-testid="sso-test-button">
                  <PlugZap className="h-3.5 w-3.5" /> 연결 테스트
                </Button>
                {sso.status === "active" ? (
                  <Button variant="outline" size="sm" onClick={p.onToggleStatus} loading={p.statusPending} disabled={p.testPending}>
                    <Pause className="h-3.5 w-3.5" /> 정지
                  </Button>
                ) : (
                  <Button variant="outline" size="sm" onClick={p.onToggleStatus} loading={p.statusPending} disabled={p.testPending}>
                    <Play className="h-3.5 w-3.5" /> 재개
                  </Button>
                )}
                <Button variant="ghost" size="sm" className="text-destructive" onClick={p.onRequestDelete} disabled={busy}>
                  <Trash2 className="h-3.5 w-3.5" /> 삭제
                </Button>
              </div>
            ) : null}

            {p.testResult ? (
              <Alert variant="success" data-testid="sso-test-result">
                연결 확인됨 — issuer <Code>{p.testResult.issuer}</Code>, JWKS 키 {p.testResult.jwks_keys}개
              </Alert>
            ) : null}
            <ErrorAlert error={p.testError} />
            <ErrorAlert error={p.statusError} />
          </>
        ) : null}

        {!p.canEdit && p.readOnlyHint ? <p className="text-xs text-muted-foreground">{p.readOnlyHint}</p> : null}
      </CardContent>
    </Card>
  );
}
