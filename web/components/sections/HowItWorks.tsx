import { Container } from "@/components/ui/Container";
import { siteConfig } from "@/lib/site-config";

const steps = [
  {
    step: "1",
    title: "Upload finished videos",
    description: "Drop in videos you've already recorded and edited — no in-app editing required.",
  },
  {
    step: "2",
    title: `${siteConfig.name} processes them`,
    description:
      "Each video is inspected, transcribed, and given a caption candidate automatically.",
  },
  {
    step: "3",
    title: "Videos fill your schedule",
    description:
      "Processed videos are queued into your posting schedule in order, on the cadence you set.",
  },
  {
    step: "4",
    title: "Publishing is handled automatically",
    description: "When a slot comes up, the video is ready to go out — no manual posting.",
  },
];

export function HowItWorks() {
  return (
    <section id="how-it-works" className="scroll-mt-16 py-20 sm:py-28">
      <Container>
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-3xl font-semibold tracking-tight text-ink">How it works</h2>
          <p className="mt-4 text-ink-muted">
            One straightforward pipeline from your camera roll to a live posting schedule.
          </p>
        </div>

        <ol className="mx-auto mt-16 grid max-w-5xl grid-cols-1 gap-8 sm:grid-cols-2 lg:grid-cols-4">
          {steps.map((item, index) => (
            <li key={item.step} className="relative pl-12">
              <span className="absolute left-0 top-0 flex h-9 w-9 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent">
                {item.step}
              </span>
              <h3 className="text-base font-semibold text-ink">{item.title}</h3>
              <p className="mt-2 text-sm text-ink-muted">{item.description}</p>
              {index < steps.length - 1 ? (
                <span
                  aria-hidden
                  className="absolute -right-4 top-4 hidden text-border lg:block"
                >
                  →
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      </Container>
    </section>
  );
}
