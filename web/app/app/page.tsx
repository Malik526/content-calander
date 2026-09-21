import Link from "next/link";
import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";

export const metadata = { title: "Home" };

/**
 * /app — Home/Dashboard shell (Milestone 3.5). A real landing page for
 * the product, not a placeholder: quick links into the three other
 * sections. No real activity feed/stats yet — those depend on data this
 * milestone deliberately does not wire up (Phase 11's guardrail).
 */
export default function AppHomePage() {
  return (
    <>
      <PageHeader title="Home" description="Your posting workflow at a glance." />
      <div className="grid gap-4 sm:grid-cols-2">
        <QuickLinkCard
          href="/app/library"
          title="Library"
          description="Videos you've batched, ready to schedule."
        />
        <QuickLinkCard
          href="/app/queue"
          title="Queue"
          description="What's scheduled and what's already gone out."
        />
        <QuickLinkCard
          href="/app/settings"
          title="Settings"
          description="Your account and connected platforms."
        />
      </div>
    </>
  );
}

function QuickLinkCard({ href, title, description }: { href: string; title: string; description: string }) {
  return (
    <Link href={href} className="block">
      <Card className="h-full transition-colors hover:border-accent/40">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        <p className="mt-1 text-sm text-ink-muted">{description}</p>
      </Card>
    </Link>
  );
}
