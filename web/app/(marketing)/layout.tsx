import { SiteHeader } from "@/components/layout/SiteHeader";
import { SiteFooter } from "@/components/layout/SiteFooter";

/**
 * Marketing-site chrome (public header/footer) — moved here from the root
 * layout (Milestone 3.5) so the product app shell (`/app/*`) can have its
 * own, structurally separate chrome instead of inheriting the marketing
 * site's header/footer. Route groups (the `(marketing)` folder name) don't
 * affect the URL — `/`, `/privacy`, `/terms` are unchanged.
 */
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <SiteHeader />
      <main className="flex-1">{children}</main>
      <SiteFooter />
    </>
  );
}
