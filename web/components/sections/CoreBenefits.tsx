import { Container } from "@/components/ui/Container";

const benefits = [
  {
    title: "Batch-oriented workflow",
    description: "Process a folder of finished videos at once instead of handling them one by one.",
  },
  {
    title: "Automatic scheduling",
    description: "Set a posting cadence once and let new videos fill in the next open slot in order.",
  },
  {
    title: "Transcript generation",
    description: "Every video is transcribed locally, giving you a text record of what was said.",
  },
  {
    title: "Caption preparation",
    description: "A caption candidate is derived from the transcript automatically for each video.",
  },
  {
    title: "First-in, first-out queue",
    description: "Videos are scheduled in the order you add them — predictable, no manual triage.",
  },
  {
    title: "Publishing integrations",
    description: "TikTok publishing is in active development, with more platforms planned after it.",
  },
];

export function CoreBenefits() {
  return (
    <section className="border-y border-border bg-surface py-20 sm:py-28">
      <Container>
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-3xl font-semibold tracking-tight text-ink">What it does today</h2>
          <p className="mt-4 text-ink-muted">
            Built for the part of publishing that&rsquo;s repetitive, not the part that&rsquo;s creative.
          </p>
        </div>

        <div className="mx-auto mt-16 grid max-w-5xl grid-cols-1 gap-x-10 gap-y-10 sm:grid-cols-2 lg:grid-cols-3">
          {benefits.map((benefit) => (
            <div key={benefit.title} className="border-l-2 border-accent-soft pl-5">
              <h3 className="text-base font-semibold text-ink">{benefit.title}</h3>
              <p className="mt-2 text-sm text-ink-muted">{benefit.description}</p>
            </div>
          ))}
        </div>
      </Container>
    </section>
  );
}
