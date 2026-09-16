import Link from "next/link";
import type { ReactNode } from "react";

type ButtonProps = {
  href: string;
  children: ReactNode;
  variant?: "primary" | "secondary";
  external?: boolean;
};

const baseClasses =
  "inline-flex items-center justify-center rounded-lg px-5 py-2.5 text-sm font-medium transition-colors";

const variantClasses = {
  primary: "bg-accent text-white hover:bg-accent-hover",
  secondary: "border border-border bg-surface text-ink hover:border-accent/40 hover:text-accent",
};

export function Button({ href, children, variant = "primary", external = false }: ButtonProps) {
  const classes = `${baseClasses} ${variantClasses[variant]}`;
  const isMailto = href.startsWith("mailto:");

  if (external && !isMailto) {
    return (
      <a href={href} className={classes} target="_blank" rel="noreferrer">
        {children}
      </a>
    );
  }
  if (isMailto) {
    return (
      <a href={href} className={classes}>
        {children}
      </a>
    );
  }
  return (
    <Link href={href} className={classes}>
      {children}
    </Link>
  );
}
