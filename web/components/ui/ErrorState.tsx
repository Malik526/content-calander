/** Reusable recoverable-error pattern (Milestone 3.5, Phase 18) — a
 * product-quality message + retry action, never a raw stack trace or a
 * blank page. `message` should already be the normalized, user-facing
 * string an ApiError carries (lib/api/client.ts), not a raw exception. */
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-3 rounded-xl border border-status-danger/20 bg-status-danger-soft px-6 py-10 text-center"
    >
      <p className="text-sm font-medium text-status-danger">{message}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="rounded-lg border border-status-danger/30 px-4 py-2 text-sm font-medium text-status-danger hover:bg-status-danger-soft"
        >
          Try again
        </button>
      ) : null}
    </div>
  );
}
