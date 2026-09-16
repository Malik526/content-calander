import type { ReactNode } from "react";

/** Centralized max-width + side-gutter wrapper — the one place page width is defined. */
export function Container({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`mx-auto w-full max-w-6xl px-6 ${className}`}>{children}</div>;
}
