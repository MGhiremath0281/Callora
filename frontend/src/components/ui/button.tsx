import { forwardRef, type ButtonHTMLAttributes } from "react";
import { Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "outline" | "ghost" | "danger" | "subtle";
type Size = "sm" | "md" | "lg" | "icon";

const variants: Record<Variant, string> = {
  primary:
    "bg-accent-600 text-white shadow-sm hover:bg-accent-700 disabled:bg-accent-300",
  secondary:
    "bg-navy-900 text-white shadow-sm hover:bg-navy-800 disabled:bg-navy-600",
  outline:
    "border border-slate-300 bg-white text-slate-700 shadow-sm hover:border-slate-400 hover:bg-slate-50 disabled:text-slate-400",
  ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
  danger:
    "bg-rose-600 text-white shadow-sm hover:bg-rose-700 disabled:bg-rose-300",
  subtle: "bg-accent-50 text-accent-700 hover:bg-accent-100",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-9 px-4 text-sm",
  lg: "h-11 px-5 text-sm",
  icon: "h-9 w-9",
};

export function buttonClasses(variant: Variant = "primary", size: Size = "md") {
  return cn(
    "inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed",
    variants[variant],
    sizes[size],
  );
}

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    { className, variant = "primary", size = "md", loading, children, disabled, ...props },
    ref,
  ) => (
    <button
      ref={ref}
      className={cn(
        "inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed",
        variants[variant],
        sizes[size],
        className,
      )}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
      {children}
    </button>
  ),
);
Button.displayName = "Button";
