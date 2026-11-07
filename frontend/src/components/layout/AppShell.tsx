import { Fragment } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Building2, LogOut, Server, Wrench } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuthStore, useIsMaster } from "@/stores/auth";
import { Badge } from "@/components/ui/Badge";
import { ROLE_LABEL } from "@/domains/common/enums";
import { API_BASE } from "@/lib/api";

interface NavItem {
  to: string;
  label: string;
  icon: typeof Building2;
  masterOnly?: boolean;
}

const NAV: NavItem[] = [
  { to: "/companies", label: "회사", icon: Building2 },
  { to: "/servers", label: "MCP 서버", icon: Server },
  { to: "/ops", label: "운영 도구", icon: Wrench, masterOnly: true },
];

const SEGMENT_LABEL: Record<string, string> = { companies: "회사", servers: "MCP 서버", ops: "운영 도구" };
const DETAIL_TITLE: Record<string, string> = { companies: "회사 상세", servers: "서버 상세" };
/** list routes: a one-line subtitle instead of a breadcrumb that would just repeat the title */
const LIST_SUBTITLE: Record<string, string> = { companies: "테넌트 회사 목록", servers: "클러스터에 등록된 서버", ops: "상태 · 키 판정 · 정합성" };

/** Page title + "회사 / <id>" subtitle from the pathname; ids are shown in mono. */
function useRouteTitle(): { title: string; parts: string[] } {
  const { pathname } = useLocation();
  const parts = pathname.split("/").filter(Boolean).map((s) => decodeURIComponent(s));
  const root = parts[0] ?? "";
  const title = parts.length > 1 ? (DETAIL_TITLE[root] ?? SEGMENT_LABEL[root] ?? root) : (SEGMENT_LABEL[root] ?? "콘솔");
  return { title, parts };
}

function Subtitle({ parts }: { parts: string[] }) {
  if (parts.length === 1 && LIST_SUBTITLE[parts[0]!]) {
    return <span className="truncate text-[13px] text-muted-foreground">{LIST_SUBTITLE[parts[0]!]}</span>;
  }
  return (
    <nav aria-label="breadcrumb" className="flex min-w-0 items-center gap-1.5 text-[13px] text-muted-foreground">
      {parts.map((seg, i) => {
        const label = SEGMENT_LABEL[seg];
        return (
          <Fragment key={`${i}-${seg}`}>
            {i > 0 ? <span className="text-subtle">/</span> : null}
            {label ? <span>{label}</span> : <span className="truncate font-mono text-xs">{seg}</span>}
          </Fragment>
        );
      })}
    </nav>
  );
}

function initials(name: string): string {
  const t = name.trim();
  return t ? t.slice(0, 2).toUpperCase() : "?";
}

export function AppShell() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const isMaster = useIsMaster();
  const navigate = useNavigate();
  const env = import.meta.env.DEV ? "dev" : "prod";
  const { title, parts } = useRouteTitle();

  const onLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="flex h-full min-h-screen">
      {/* slim icon-only rail (KakaoTalk-desktop style): no brand block, no text */}
      <aside className="flex w-16 shrink-0 flex-col items-center bg-sidebar pb-3 pt-3" aria-label="주 메뉴">
        <nav className="flex flex-col items-center gap-1">
          {NAV.filter((n) => !n.masterOnly || isMaster).map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              aria-label={n.label}
              title={n.label}
              className={({ isActive }) =>
                cn(
                  "flex h-10 w-10 items-center justify-center rounded-lg transition-colors duration-200",
                  isActive ? "bg-primary text-primary-foreground" : "text-sidebar-muted hover:bg-sidebar-hover hover:text-sidebar-foreground",
                )
              }
            >
              <n.icon className="h-5 w-5" aria-hidden />
            </NavLink>
          ))}
        </nav>
        {user ? (
          <div className="mt-auto flex flex-col items-center gap-1">
            <div className="flex h-10 w-10 items-center justify-center">
              <div
                className="flex h-9 w-9 items-center justify-center rounded-full bg-sidebar-hover text-xs font-bold text-sidebar-foreground"
                title={`${user.name} · ${user.email}`}
                aria-label={`${user.name} · ${user.email}`}
                role="img"
              >
                {initials(user.name)}
              </div>
            </div>
            <button
              type="button"
              onClick={onLogout}
              aria-label="로그아웃"
              title="로그아웃"
              className="flex h-10 w-10 items-center justify-center rounded-lg text-sidebar-muted transition-colors duration-200 hover:bg-sidebar-hover hover:text-sidebar-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <LogOut className="h-5 w-5" aria-hidden />
            </button>
          </div>
        ) : null}
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        {/* same white as the page; the hairline is drawn inside the 64px box so the title centres at y=32 like the first rail icon */}
        <header
          className="relative z-20 flex h-16 shrink-0 items-center justify-between gap-4 bg-card px-6 after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-border"
          data-testid="app-header"
        >
          <div className="flex min-w-0 items-center gap-4">
            <h1 className="shrink-0 text-[22px] font-bold leading-none tracking-tight" data-testid="app-title">{title}</h1>
            <span className="h-3 w-px shrink-0 bg-border" aria-hidden />
            <Subtitle parts={parts} />
          </div>
          <div className="flex shrink-0 items-center gap-4 text-xs leading-none text-muted-foreground" data-testid="app-header-right">
            <Badge variant="outline" dot="accent" className="font-mono uppercase" title={`API: ${API_BASE}`}>
              {env}
            </Badge>
            {user ? (
              <span className="hidden sm:inline">
                {user.name} <span className="text-subtle">·</span> {ROLE_LABEL[user.role] ?? user.role}
                {!isMaster ? (
                  <>
                    {" "}
                    <span className="text-subtle">·</span>{" "}
                    <span className="font-mono" title={user.company_id}>
                      {user.company_id}
                    </span>
                  </>
                ) : null}
              </span>
            ) : null}
          </div>
        </header>
        <main className="min-w-0 flex-1 overflow-auto">
          <div className="w-full px-6 py-6" data-testid="app-content">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
