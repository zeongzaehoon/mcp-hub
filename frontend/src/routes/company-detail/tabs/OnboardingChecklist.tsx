import type { ReactNode } from "react";
import { ArrowRight, CheckCircle2, Circle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { useApiKeysInfinite } from "@/domains/apikeys/queries";
import { useBindingsInfinite } from "@/domains/bindings/queries";
import { usePoliciesInfinite } from "@/domains/policies/queries";
import { useSso } from "@/domains/sso/queries";
import { useUsersPage } from "@/domains/users/queries";
import { useWorkspace } from "@/domains/workspace/queries";
import type { CompanyTab } from "../CompanyDetailPresenter";

interface OnboardingChecklistProps {
  companyId: string;
  onTabChange: (tab: CompanyTab) => void;
}

interface Step {
  label: string;
  done: boolean;
  detail: ReactNode;
  action?: ReactNode;
}

/** Four-row "getting started" guide for a company. Reads the first page of each list; done/todo only. */
export function OnboardingChecklist({ companyId, onTabChange }: OnboardingChecklistProps) {
  const sso = useSso(companyId);
  const workspace = useWorkspace(companyId);
  const bindings = useBindingsInfinite(companyId);
  const policies = usePoliciesInfinite(companyId);
  const users = useUsersPage(companyId, {});
  // key names are `sso:<client>` when issued by a connector SSO login
  const ssoKeys = useApiKeysInfinite(companyId, { q: "sso:", status: "" });

  const hasWorkspace = !!workspace.data;
  const loginDone = !!sso.data || hasWorkspace;
  const bindingDone = (bindings.data?.pages[0]?.items ?? []).some((b) => b.status === "active");
  const policyDone = (policies.data?.pages[0]?.items ?? []).some((x) => x.status === "active");
  const employeeDone =
    (users.data?.items ?? []).some((u) => u.source === "workspace") ||
    (ssoKeys.data?.pages[0]?.items ?? []).some((k) => k.name.startsWith("sso:"));

  const jump = (tab: CompanyTab, label: string) => (
    <Button variant="ghost" size="sm" onClick={() => onTabChange(tab)}>
      {label} <ArrowRight className="h-3.5 w-3.5" />
    </Button>
  );

  const steps: Step[] = [
    {
      label: "Workspace 연동",
      done: loginDone,
      detail: loginDone
        ? "디렉터리 연동 또는 직원 로그인 공급자가 설정되어 있습니다."
        : "운영자가 아래 'Google Workspace 연동' 카드에서 서비스 계정을 등록합니다.",
    },
    {
      label: "데이터소스 바인딩",
      done: bindingDone,
      detail: bindingDone ? "활성 서버 바인딩이 있습니다." : "회사 테넌트를 MCP 서버에 연결합니다.",
      action: jump("bindings", "서버 바인딩"),
    },
    {
      label: "팀 정책",
      done: policyDone,
      detail: policyDone ? "활성 팀 정책이 있습니다." : "팀(또는 전원) 단위로 서버 권한·도구를 부여합니다.",
      action: (
        <span className="inline-flex flex-wrap items-center gap-1">
          {jump("policies", "정책")}
          {!hasWorkspace ? <span className="text-xs text-muted-foreground">또는</span> : null}
          {!hasWorkspace ? jump("access", "사용자별 접근 부여") : null}
        </span>
      ),
    },
    {
      label: "직원 연결 안내",
      done: employeeDone,
      detail: "직원은 MCP 클라이언트의 '회사 계정으로 로그인'을 쓰거나, 운영자가 API 키 탭에서 키를 발급해 전달합니다.",
    },
  ];

  return (
    <Card className="lg:col-span-2" data-testid="onboarding-checklist">
      <CardHeader>
        <CardTitle>시작하기</CardTitle>
        <span className="text-xs text-muted-foreground">{steps.filter((s) => s.done).length} / {steps.length} 완료</span>
      </CardHeader>
      <CardContent className="py-2">
        <ol className="divide-y divide-border">
          {steps.map((s, i) => (
            <li key={s.label} className="flex items-start gap-3 py-2.5" data-testid={`onboarding-step-${i + 1}`} data-done={s.done}>
              {s.done ? (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-label="완료" />
              ) : (
                <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-label="할 일" />
              )}
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">
                  {i + 1}. {s.label}
                </div>
                <div className="text-xs text-muted-foreground">{s.detail}</div>
              </div>
              {s.action ? <div className="shrink-0">{s.action}</div> : null}
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}
