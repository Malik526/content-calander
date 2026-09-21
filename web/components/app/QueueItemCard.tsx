import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { formatScheduledAt, platformPostStatusPresentation } from "@/lib/status";
import type { QueueItem } from "@/lib/api/types";

/** Reusable queue row — used by /app/queue whenever there are real items,
 * and tested directly with sample data so the layout is verified even
 * while the live page ships empty by default (Milestone 3.5). */
export function QueueItemCard({ item }: { item: QueueItem }) {
  const presentation = platformPostStatusPresentation[item.status];
  return (
    <Card className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-ink">{item.videoTitle}</p>
        <p className="mt-0.5 text-xs text-ink-muted">{formatScheduledAt(item.scheduledAt)} · TikTok</p>
      </div>
      <Badge tone={presentation.tone}>{presentation.label}</Badge>
    </Card>
  );
}
