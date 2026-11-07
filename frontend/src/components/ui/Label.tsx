import type { LabelHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";

interface LabelProps extends LabelHTMLAttributes<HTMLLabelElement> {
  required?: boolean;
}

/** knitlog `.be-label`: 13px semibold grey-500, 4px left margin, red required mark. */
export function Label({ className, required, children, ...props }: LabelProps) {
  return (
    <label className={cn("ml-1 text-[13px] font-semibold leading-tight text-muted-foreground", className)} {...props}>
      {children}
      {required ? (
        <span className="text-destructive" aria-hidden>
          {" "}
          *
        </span>
      ) : null}
    </label>
  );
}

export function FieldHint({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn("ml-1 text-xs text-muted-foreground", className)}>{children}</p>;
}

export function FieldError({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p role="alert" className={cn("ml-1 text-xs text-destructive", className)}>
      {children}
    </p>
  );
}

interface FieldProps {
  label: string;
  hint?: ReactNode;
  /** red text under the control; implies an error state */
  error?: ReactNode;
  required?: boolean;
  htmlFor?: string;
  children: ReactNode;
}

export function Field({ label, hint, error, required, children, htmlFor }: FieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={htmlFor} required={required}>
        {label}
      </Label>
      {children}
      {error ? <FieldError>{error}</FieldError> : hint ? <FieldHint>{hint}</FieldHint> : null}
    </div>
  );
}
