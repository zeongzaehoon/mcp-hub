import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-5 w-5 animate-spin text-muted-foreground", className)} aria-label="로딩 중" />;
}

export function PageSpinner() {
  return (
    <div className="flex h-40 items-center justify-center">
      <Spinner className="h-6 w-6" />
    </div>
  );
}
