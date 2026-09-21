/**
 * lib/supabase/client.ts — the one Supabase Auth client this app creates
 * (Milestone 3.6). Browser-only, PKCE flow (supabase-js's default for a
 * public client with no server component), reused by lib/session.tsx and
 * lib/api/client.ts rather than each constructing their own.
 *
 * NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY are meant to be
 * public — the anon key is a public API key by Supabase's own design
 * (Row Level Security, not secrecy, is what protects data behind it); it
 * is never a substitute for the service-role key, which never has any
 * reason to exist in this package (see ../../.env.example).
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

export const isSupabaseConfigured = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);

let client: SupabaseClient | null = null;

/** Lazily creates the client on first use — avoids throwing at module-load
 * time (e.g. during a build step) purely because env vars aren't set yet;
 * callers that actually need a session (lib/session.tsx) surface a clear
 * error instead if isSupabaseConfigured is false. */
export function getSupabaseClient(): SupabaseClient {
  if (!isSupabaseConfigured) {
    throw new Error(
      "Supabase is not configured — set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY " +
        "(see .env.example).",
    );
  }
  if (!client) {
    client = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
      auth: { flowType: "pkce", persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
  }
  return client;
}
