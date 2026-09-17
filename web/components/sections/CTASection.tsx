import { Container } from "@/components/ui/Container";
import { Button } from "@/components/ui/Button";
import { siteConfig } from "@/lib/site-config";

export function CTASection() {
  return (
    <section id="cta" className="scroll-mt-16 border-t border-border bg-surface py-20 sm:py-28">
      <Container>
        <div className="mx-auto max-w-2xl rounded-2xl border border-border bg-background px-8 py-12 text-center">
          <h2 className="text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
            {siteConfig.name} is in active development.
          </h2>
          <p className="mt-4 text-ink-muted">
            If you post short-form video regularly and want to try it early, reach out and we&rsquo;ll
            follow up as access opens.
          </p>
          <div className="mt-8">
            <Button href={`mailto:${siteConfig.contactEmail}`} variant="primary" external>
              Request early access
            </Button>
          </div>
        </div>
      </Container>
    </section>
  );
}
