import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useIsAuthenticated } from "@/stores/auth";

export function RequireAuth({ children }: { children: ReactNode }) {
  const authed = useIsAuthenticated();
  const location = useLocation();
  if (!authed) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

/** Master-only route guard: renders a friendly notice instead of hitting 403s. */
export function RequireMaster({ children, isMaster }: { children: ReactNode; isMaster: boolean }) {
  if (!isMaster) {
    return (
      <div className="rounded-lg border border-border bg-card p-8 text-center text-sm text-muted-foreground">
        이 페이지는 마스터 계정만 사용할 수 있습니다.
      </div>
    );
  }
  return <>{children}</>;
}
