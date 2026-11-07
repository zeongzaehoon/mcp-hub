import { useState, type ReactNode } from "react";
import { Check, Copy, KeyRound, X } from "lucide-react";
import { Button } from "@/components/ui/Button";

interface CopyOnceBannerProps {
  rawKey: string;
  title?: string;
  onDismiss: () => void;
  /** extra content under the key, e.g. how to connect */
  children?: ReactNode;
}

export function CopyOnceBanner({ rawKey, title = "새 API 키가 발급되었습니다", onDismiss, children }: CopyOnceBannerProps) {
  const [copied, setCopied] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(rawKey);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard may be unavailable (non-secure context); the key stays visible for manual copy
      setCopied(false);
    }
  };

  return (
    <div className="rounded-lg border border-primary/30 bg-card p-4" role="status">
      <div className="flex items-start gap-3">
        <KeyRound className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden />
        <div className="min-w-0 flex-1 space-y-2">
          <p className="font-semibold">{title}</p>
          <p className="text-sm">
            이 키는 <strong>지금 한 번만</strong> 표시됩니다. 창을 닫으면 다시 확인할 수 없으니 안전한 곳에 저장하세요.
          </p>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 select-all break-all rounded border border-border-strong bg-muted px-2 py-1.5 font-mono text-xs">
              {rawKey}
            </code>
            <Button variant="outline" size="sm" onClick={copy}>
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              {copied ? "복사됨" : "복사"}
            </Button>
          </div>
          {children}
          {confirmClose ? (
            <div className="flex items-center gap-2 text-sm">
              <span>키를 저장했습니까?</span>
              <Button size="sm" variant="destructive" onClick={onDismiss}>
                저장했습니다, 닫기
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmClose(false)}>
                아니오
              </Button>
            </div>
          ) : null}
        </div>
        <Button variant="ghost" size="icon" onClick={() => setConfirmClose(true)} aria-label="닫기">
          <X className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
