import type { StatusTone } from "@/lib/status";

const toneClasses: Record<StatusTone, string> = {
  pending: "bg-status-pending-soft text-status-pending",
  progress: "bg-status-progress-soft text-status-progress",
  success: "bg-status-success-soft text-status-success",
  danger: "bg-status-danger-soft text-status-danger",
};

export function Badge({ tone, children }: { tone: StatusTone; children: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${toneClasses[tone]}`}>
      {children}
    </span>
  );
}
