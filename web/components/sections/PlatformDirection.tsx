import { Container } from "@/components/ui/Container";

const platforms = [
  { name: "TikTok", status: "In development", note: "Direct Post integration is being built and tested now." },
  { name: "Instagram", status: "Planned", note: "Being considered for a future release." },
  { name: "YouTube Shorts", status: "Planned", note: "Being considered for a future release." },
];

export function PlatformDirection() {
  return (
    <section id="platform-direction" className="scroll-mt-16 py-20 sm:py-28">
      <Container>
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-3xl font-semibold tracking-tight text-ink">Where publishing is headed</h2>
          <p className="mt-4 text-ink-muted">
            Content Automation is being built platform by platform, starting with TikTok.
          </p>
        </div>

        <div className="mx-auto mt-16 max-w-2xl divide-y divide-border overflow-hidden rounded-xl border border-border bg-surface">
          {platforms.map((platform) => (
            <div key={platform.name} className="flex items-center justify-between gap-4 px-6 py-5">
              <div>
                <p className="text-sm font-semibold text-ink">{platform.name}</p>
                <p className="text-sm text-ink-muted">{platform.note}</p>
              </div>
              <span className="whitespace-nowrap rounded-full bg-accent-soft px-3 py-1 text-xs font-medium text-accent">
                {platform.status}
              </span>
            </div>
          ))}
        </div>
      </Container>
    </section>
  );
}
