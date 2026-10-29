import type { ReactNode } from "react";
import { Link, type LinkProps } from "react-router-dom";

import { buttonClasses } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * A router link that looks like a button. Kept separate from <Button> so the
 * DOM never nests an anchor inside a button.
 */
export function ButtonLink({
  to,
  variant = "primary",
  size = "md",
  className,
  children,
  ...props
}: Omit<LinkProps, "to" | "className"> & {
  to: string;
  variant?: "primary" | "secondary" | "outline" | "ghost" | "danger" | "subtle";
  size?: "sm" | "md" | "lg" | "icon";
  className?: string;
  children: ReactNode;
}) {
  return (
    <Link to={to} className={cn(buttonClasses(variant, size), className)} {...props}>
      {children}
    </Link>
  );
}
