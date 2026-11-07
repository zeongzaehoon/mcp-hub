import { Pause, Play, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import type { Status } from "@/domains/common/enums";

export type StatusTransition = "suspend" | "reactivate" | "delete";

interface StatusActionsProps {
  status: Status;
  onAction: (transition: StatusTransition) => void;
  disabled?: boolean;
  size?: "sm" | "icon";
}

/** Renders the transitions available from the current status. Deleted is terminal (no actions). */
export function StatusActions({ status, onAction, disabled = false, size = "sm" }: StatusActionsProps) {
  if (status === "deleted") return <span className="text-xs text-muted-foreground">삭제됨 (변경 불가)</span>;
  const iconOnly = size === "icon";
  return (
    <span className="inline-flex items-center gap-1">
      {status === "active" ? (
        <Button variant="outline" size={size} disabled={disabled} onClick={() => onAction("suspend")} title="일시중지">
          <Pause className="h-3.5 w-3.5" />
          {iconOnly ? null : "일시중지"}
        </Button>
      ) : (
        <Button variant="outline" size={size} disabled={disabled} onClick={() => onAction("reactivate")} title="재활성화">
          <Play className="h-3.5 w-3.5" />
          {iconOnly ? null : "재활성화"}
        </Button>
      )}
      <Button variant="destructive" size={size} disabled={disabled} onClick={() => onAction("delete")} title="삭제">
        <Trash2 className="h-3.5 w-3.5" />
        {iconOnly ? null : "삭제"}
      </Button>
    </span>
  );
}

export const TRANSITION_TARGET: Record<StatusTransition, Status> = {
  suspend: "suspended",
  reactivate: "active",
  delete: "deleted",
};

export const TRANSITION_LABEL: Record<StatusTransition, string> = {
  suspend: "일시중지",
  reactivate: "재활성화",
  delete: "삭제",
};
