/**
 * client.ts — the one place frontend code makes backend HTTP calls
 * (Milestone 3.5, Phase 12).
 *
 * No component should call `fetch()` directly — every request goes
 * through `apiRequest()` so error handling, the base URL, and auth-token
 * attachment all live in exactly one place. As of Milestone 3.6, a real
 * FastAPI backend exists (src/content_automation/api/) and this module is
 * genuinely used — see lib/api/tiktok.ts for the first real typed calls.
 *
 * `accessToken` (Milestone 3.6) attaches `Authorization: Bearer
 * <accessToken>` when provided — the caller supplies it (typically
 * `useSession().accessToken`), this module never imports lib/session.tsx
 * or the Supabase client itself, so it stays framework/session-agnostic
 * (see docs/decisions/0010-frontend-app-shell.md "Future native
 * compatibility assumption" — a future native client can reuse this
 * exact module with its own token source).
 *
 * Deliberately assumes `browser -> API -> Postgres/storage`, never
 * `browser -> Supabase directly` — see
 * docs/decisions/0010-frontend-app-shell.md "Backend API Boundary
 * Alignment". NEXT_PUBLIC_API_BASE_URL is the only backend-related value
 * ever read here; nothing server-only (DATABASE_URL, a service-role key)
 * has any reason to exist in this package at all — anything with
 * NEXT_PUBLIC_ in its name is bundled into the browser and must be
 * treated as public.
 */

// Trailing slash(es) stripped — every call site's `path` already starts
// with "/" (see lib/api/platforms.ts), so a base URL configured with one
// (e.g. NEXT_PUBLIC_API_BASE_URL=https://api.example.com/) would otherwise
// silently double it into "https://api.example.com//api/..." — FastAPI/
// Starlette does not treat that as equivalent to a single slash, so it
// 404s. Normalizing here is defensive: correct either way the deployment
// env var happens to be set, no deploy-config guess required.
const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/+$/, "");

export class ApiError extends Error {
  readonly status: number | null;
  readonly reasonCode: string;

  constructor(message: string, { status = null, reasonCode = "UNKNOWN" }: { status?: number | null; reasonCode?: string } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.reasonCode = reasonCode;
  }
}

export interface ApiRequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  accessToken?: string | null;
}

/**
 * Typed fetch wrapper. Normalizes every failure mode (network failure,
 * non-2xx response, malformed JSON) into an ApiError with a stable
 * `reasonCode` — mirrors the backend's own PublishError/StorageError
 * shape (docs/decisions/0006-tiktok-publisher-foundation.md,
 * docs/decisions/0009-object-storage-media-lifecycle.md) so error
 * handling reads the same way on both sides of the stack.
 */
export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const { body, headers, accessToken, ...rest } = options;

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...rest,
      headers: {
        "Content-Type": "application/json",
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Could not reach the server. Check your connection and try again.", {
      reasonCode: "NETWORK_ERROR",
    });
  }

  if (!response.ok) {
    let message = `Request failed (HTTP ${response.status}).`;
    try {
      // FastAPI's HTTPException always serializes as {"detail": "..."} —
      // this backend never sends {"message": ...}. The `message` fallback
      // is kept only in case a future non-FastAPI-shaped error response
      // uses it; `detail` is checked first since it's what every real
      // backend error actually carries, and was previously never read at
      // all, silently discarding the real reason behind every backend
      // error (401/404/409/etc.) in favor of the generic message below.
      const errorBody = (await response.json()) as { detail?: string; message?: string };
      if (errorBody?.detail) message = errorBody.detail;
      else if (errorBody?.message) message = errorBody.message;
    } catch {
      // response body wasn't JSON — keep the generic message above
    }
    throw new ApiError(message, { status: response.status, reasonCode: "HTTP_ERROR" });
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("The server returned an unexpected response.", { reasonCode: "MALFORMED_RESPONSE" });
  }
}
