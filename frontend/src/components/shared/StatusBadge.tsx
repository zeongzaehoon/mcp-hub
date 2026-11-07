import { Badge, type BadgeDot, type BadgeProps } from "@/components/ui/Badge";
import { STATUS_LABEL, type Status } from "@/domains/common/enums";

/** active = blue-soft pill with an accent dot, suspended = grey pill with a hollow dot, deleted = grey pill with a grey dot. */
const styleByStatus: Record<Status, { variant: NonNullable<BadgeProps["variant"]>; dot: BadgeDot }> = {
  active: { variant: "success", dot: "accent" },
  suspended: { variant: "warning", dot: "hollow" },
  deleted: { variant: "warning", dot: "muted" },
};

export function StatusBadge({ status }: { status: Status }) {
  const s = styleByStatus[status] ?? { variant: "secondary" as const, dot: "muted" as const };
  return (
    <Badge variant={s.variant} dot={s.dot}>
      {STATUS_LABEL[status] ?? status}
    </Badge>
  );
}
