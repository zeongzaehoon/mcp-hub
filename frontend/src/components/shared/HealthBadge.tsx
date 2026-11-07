import { Badge } from "@/components/ui/Badge";
import type { HealthOut } from "@/domains/ops/types";

/** ok → accent dot, down → destructive (an error state), configured but unchecked → hollow dot; null → "미설정" text. */
export function HealthBadge({ health, showLatency = true }: { health: HealthOut | null | undefined; showLatency?: boolean }) {
  if (!health) return <span className="text-xs text-muted-foreground">미설정</span>;
  if (health.ok === true) {
    return (
      <Badge variant="success" dot="accent" title={health.checked_at ?? undefined}>
        정상{showLatency && health.latency_ms !== null ? <span className="font-mono text-muted-foreground">{health.latency_ms}ms</span> : null}
      </Badge>
    );
  }
  if (health.ok === false) {
    return (
      <Badge variant="danger" dot="danger" title={health.error ?? undefined}>
        다운
      </Badge>
    );
  }
  return (
    <Badge variant="warning" dot="hollow">
      미확인
    </Badge>
  );
}
