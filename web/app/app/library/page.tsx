import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

export const metadata = { title: "Library" };

/**
 * /app/library — shell only (Milestone 3.5). Real behavior ("no videos
 * yet") by default, not a populated mock — batch upload itself is
 * Milestone 3.7's scope, explicitly not pulled forward here (see the
 * milestone brief's own guardrails). The empty-state action is
 * disabled-looking/inert on purpose: it signals where upload will live
 * without implementing it.
 */
export default function LibraryPage() {
  return (
    <>
      <PageHeader title="Library" description="Videos you've batched and processed." />
      <EmptyState
        title="No videos yet"
        description="Once batch upload ships, your processed videos will show up here, ready to schedule."
        action={
          <Button href="#" variant="secondary">
            Upload coming soon
          </Button>
        }
      />
    </>
  );
}
