import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/** knitlog `.be-btn`: 38px / radius 5px / 14px bold. Filled variants go grey when disabled; outlines fade. */
export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-[5px] text-sm font-bold leading-none transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 disabled:pointer-events-none",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow-button hover:bg-primary/90 disabled:bg-disabled disabled:text-disabled-foreground disabled:shadow-none",
        secondary: "bg-foreground text-card hover:bg-sidebar-hover disabled:bg-disabled disabled:text-disabled-foreground",
        outline: "border border-border-strong bg-card text-muted-foreground hover:bg-muted disabled:opacity-50",
        ghost: "text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50",
        destructive: "border border-destructive/30 bg-card text-destructive hover:bg-destructive-soft disabled:opacity-50",
        link: "text-primary underline-offset-4 hover:underline disabled:opacity-50",
      },
      size: {
        sm: "h-8 px-3 text-[13px]",
        md: "h-[38px] px-4",
        lg: "h-11 px-6 text-base",
        icon: "h-8 w-8",
      },
    },
    defaultVariants: { variant: "default", size: "md" },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, loading = false, disabled, children, type = "button", ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={cn(buttonVariants({ variant, size }), className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
      {children}
    </button>
  ),
);
Button.displayName = "Button";
