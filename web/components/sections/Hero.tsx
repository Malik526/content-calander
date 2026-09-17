import { Container } from "@/components/ui/Container";
import { Button } from "@/components/ui/Button";
import { siteConfig } from "@/lib/site-config";

export function Hero() {
  return (
    <section className="border-b border-border bg-surface py-20 sm:py-28">
      <Container>
        <div className="mx-auto max-w-3xl text-center">
          <span className="inline-flex items-center rounded-full bg-accent-soft px-3 py-1 text-xs font-medium text-accent">
            Early development
          </span>
          <h1 className="mt-6 text-4xl font-semibold tracking-tight text-ink sm:text-5xl">
            Turn finished videos into a running posting schedule.
          </h1>
          <p className="mt-6 text-lg text-ink-muted">{siteConfig.description}</p>
          <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
            <Button href="/#cta" variant="primary">
              Request early access
            </Button>
            <Button href="/#how-it-works" variant="secondary">
              See how it works
            </Button>
          </div>
        </div>
      </Container>
    </section>
  );
}
