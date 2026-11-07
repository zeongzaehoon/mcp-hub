import type { FormEvent } from "react";
import { Layers } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Field } from "@/components/ui/Label";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { API_BASE } from "@/lib/api";

interface LoginPresenterProps {
  email: string;
  password: string;
  onEmailChange: (v: string) => void;
  onPasswordChange: (v: string) => void;
  onSubmit: (e: FormEvent) => void;
  isPending: boolean;
  error: unknown;
}

/** knitlog split login: dark canvas, huge brand tagline on the left, a white card (376px) on the right. */
export function LoginPresenter(p: LoginPresenterProps) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-sidebar p-6 text-sidebar-foreground">
      <div className="grid w-full max-w-5xl items-center gap-12 md:grid-cols-[auto_376px] md:gap-16">
        <div className="min-w-0 space-y-6">
          <div className="inline-flex items-center gap-2.5 rounded-full bg-sidebar-hover px-3 py-1.5 text-xs font-semibold text-sidebar-muted">
            <span className="flex h-5 w-5 items-center justify-center rounded bg-card text-sidebar">
              <Layers className="h-3 w-3" aria-hidden />
            </span>
            FindTheGap
          </div>
          <h1 className="text-5xl font-extrabold leading-none tracking-tight md:text-7xl">
            MCP
            <br />
            Cluster
          </h1>
          <p className="max-w-md text-xl font-semibold leading-[1.3] text-sidebar-foreground/90 md:text-2xl">
            사내 AX팀을 위한
            <br />
            MCP 서버 운영 콘솔
          </p>
          <p className="font-mono text-xs text-sidebar-muted">{API_BASE}</p>
        </div>

        <div className="w-full rounded-2xl bg-card p-7 text-foreground shadow-popup">
          <form onSubmit={p.onSubmit} className="space-y-5">
            <div>
              <h2 className="text-[22px] font-bold tracking-tight">관리자 로그인</h2>
              <p className="mt-1 text-[13px] text-muted-foreground">마스터 또는 회사 관리자 계정</p>
            </div>
            <Field label="이메일" htmlFor="login-email">
              <Input
                id="login-email"
                type="email"
                autoComplete="username"
                value={p.email}
                onChange={(e) => p.onEmailChange(e.target.value)}
                placeholder="admin@company.com"
                required
                autoFocus
              />
            </Field>
            <Field label="비밀번호" htmlFor="login-password">
              <Input
                id="login-password"
                type="password"
                autoComplete="current-password"
                value={p.password}
                onChange={(e) => p.onPasswordChange(e.target.value)}
                required
              />
            </Field>
            <ErrorAlert error={p.error} />
            <Button type="submit" className="w-full" loading={p.isPending}>
              로그인
            </Button>
          </form>
          <p className="mt-6 text-center text-xs text-subtle">FindTheGap · admin console</p>
        </div>
      </div>
    </div>
  );
}
