import { Badge } from "@/components/ui/Badge";
import type { WorkspaceSource } from "@/domains/workspace/types";

/** "Workspace" = managed by the Google Workspace directory sync; "수동" = created in the console. */
export function SourceBadge({ source }: { source: WorkspaceSource }) {
  return source === "workspace" ? (
    <Badge variant="secondary" title="Google Workspace 디렉터리에서 동기화됨">
      Workspace
    </Badge>
  ) : (
    <Badge variant="outline">수동</Badge>
  );
}
