import { Alert } from "@/components/ui/Alert";
import { getErrorMessage, isForbidden } from "@/lib/api";

export function ErrorAlert({ error, className }: { error: unknown; className?: string }) {
  if (!error) return null;
  const message = isForbidden(error) ? `권한이 없습니다: ${getErrorMessage(error)}` : getErrorMessage(error);
  return (
    <Alert variant="error" className={className}>
      {message}
    </Alert>
  );
}
