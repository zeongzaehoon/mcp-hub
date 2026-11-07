import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** red border (validation failure) */
  invalid?: boolean;
}

/** knitlog `.be-input`: radius 5px, 1px grey-200 border, 10px 14px padding, blue 1px focus. */
export const inputClass =
  "flex w-full rounded-[5px] border border-border-strong bg-card px-3.5 py-2.5 text-sm leading-[18px] text-foreground transition-colors placeholder:text-subtle focus-visible:border-primary focus-visible:outline-none disabled:cursor-not-allowed disabled:border-disabled disabled:bg-muted disabled:text-subtle";

export const Input = forwardRef<HTMLInputElement, InputProps>(({ className, type = "text", invalid, ...props }, ref) => (
  <input
    ref={ref}
    type={type}
    aria-invalid={invalid || undefined}
    className={cn(inputClass, "h-[38px]", invalid && "border-destructive focus-visible:border-destructive", className)}
    {...props}
  />
));
Input.displayName = "Input";
