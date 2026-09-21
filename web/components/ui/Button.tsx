import Link from "next/link";
import type { ReactNode } from "react";

type LinkButtonProps = {
  href: string;
  children: ReactNode;
  variant?: "primary" | "secondary";
  external?: boolean;
  type?: never;
};

type ActionButtonProps = {
  type: "button";
  children: ReactNode;
  variant?: "primary" | "secondary";
  onClick: () => void;
  disabled?: boolean;
  href?: never;
};

type ButtonProps = LinkButtonProps | ActionButtonProps;

const baseClasses =
  "inline-flex items-center justify-center rounded-lg px-5 py-2.5 text-sm font-medium transition-colors disabled:opacity-60 disabled:cursor-not-allowed";

const variantClasses = {
  primary: "bg-accent text-white hover:bg-accent-hover",
  secondary: "border border-border bg-surface text-ink hover:border-accent/40 hover:text-accent",
};

/**
 * A real action (onClick, e.g. "Connect"/"Disconnect" — Milestone 3.6)
 * renders a real `<button type="button">`, never a link styled to look
 * like one — an href="#" + preventDefault() button is both bad
 * accessibility (screen readers announce it as a link) and a genuine
 * navigation hazard. Pass `href` for navigation, `type="button"` +
 * `onClick` for an action; never both.
 */
export function Button(props: ButtonProps) {
  const classes = `${baseClasses} ${variantClasses[props.variant ?? "primary"]}`;

  if (props.type === "button") {
    return (
      <button type="button" className={classes} onClick={props.onClick} disabled={props.disabled}>
        {props.children}
      </button>
    );
  }

  const { href, children, external = false } = props;
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
