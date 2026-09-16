import Link from "next/link";
import { Container } from "@/components/ui/Container";
import { siteConfig } from "@/lib/site-config";

export function SiteFooter() {
  const year = new Date().getFullYear();

  return (
    <footer className="border-t border-border bg-surface">
      <Container className="flex flex-col gap-4 py-10 text-sm text-ink-muted sm:flex-row sm:items-center sm:justify-between">
        <p>
          © {year} {siteConfig.name}. In active development.
        </p>
        <nav className="flex gap-6">
          <Link href="/privacy" className="hover:text-ink">
            Privacy Policy
          </Link>
          <Link href="/terms" className="hover:text-ink">
            Terms of Service
          </Link>
          <a href={`mailto:${siteConfig.contactEmail}`} className="hover:text-ink">
            Contact
          </a>
        </nav>
      </Container>
    </footer>
  );
}
