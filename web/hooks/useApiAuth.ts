/**
 * useApiAuth — what every server-state hook needs from the session: the
 * user id that scopes its cache key and the bearer token for the request
 * (Milestone 3.15). The token is read at fetch time and is never part of a
 * query key (see lib/query/keys.ts).
 *
 * A null token means the dev-only mock session (lib/session.tsx), which has
 * no backend to call; hooks then return an empty default instead of
 * fetching, matching what each page showed before.
 */

import { useSession } from "@/lib/session";

export function useApiAuth(): { userId: string; accessToken: string | null } {
  const { user, accessToken } = useSession();
  return { userId: user?.id ?? "anonymous", accessToken };
}
