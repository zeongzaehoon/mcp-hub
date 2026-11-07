import { Badge } from "@/components/ui/Badge";
import { PERMISSION_LABEL, type Permission } from "@/domains/common/enums";

export function PermissionBadges({ permissions }: { permissions: readonly Permission[] }) {
  if (permissions.length === 0) return <span className="text-xs text-muted-foreground">-</span>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {permissions.map((p) => (
        <Badge key={p} variant="outline">
          {PERMISSION_LABEL[p] ?? p}
        </Badge>
      ))}
    </span>
  );
}
