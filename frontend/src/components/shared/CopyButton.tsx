import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

interface CopyButtonProps {
  value: string;
  /** accessible name, e.g. "클라이언트 ID 복사" */
  label: string;
  /** icon-only (32px) instead of "복사" text */
  iconOnly?: boolean;
  className?: string;
}

export function CopyButton({ value, label, iconOnly = false, className }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard may be unavailable (non-secure context); the value stays visible for manual copy
      setCopied(false);
    }
  };
  const Icon = copied ? Check : Copy;
  return (
    <Button variant="outline" size={iconOnly ? "icon" : "sm"} onClick={copy} aria-label={label} title={label} className={cn("shrink-0", className)}>
      <Icon className="h-3.5 w-3.5" />
      {iconOnly ? null : copied ? "복사됨" : "복사"}
    </Button>
  );
}
