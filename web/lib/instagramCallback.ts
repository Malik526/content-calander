/**
 * instagramCallback.ts — reading the Instagram OAuth callback's outcome on
 * Settings (Milestone 4.1).
 *
 * The backend callback (api/routes/platforms_instagram.py) redirects back
 * with `?instagram=<outcome>`. takeInstagramCallbackOutcome() reads that
 * parameter once and removes only it from the address bar (other query
 * parameters stay), so a reload doesn't repeat the message.
 * instagramCallbackNotice() maps the outcome to what Settings shows; an
 * unrecognized value gets a generic error, never the raw value.
 */

export const INSTAGRAM_CALLBACK_PARAM = "instagram";

export type InstagramCallbackNotice = { tone: "success" | "error"; message: string };

const MESSAGES = new Map<string, InstagramCallbackNotice>([
  ["connected", { tone: "success", message: "Instagram connected." }],
  ["denied", { tone: "error", message: "Instagram authorization was denied or cancelled." }],
  ["invalid_state", { tone: "error", message: "The sign-in attempt could not be verified. Try connecting Instagram again." }],
  ["expired_state", { tone: "error", message: "That Instagram connection attempt expired. Try connecting again." }],
  ["exchange_failed", { tone: "error", message: "Instagram could not be reached to finish connecting. Try again." }],
  ["unavailable", { tone: "error", message: "Connecting Instagram isn't available right now. Try again later." }],
]);

const FALLBACK: InstagramCallbackNotice = { tone: "error", message: "Something went wrong connecting Instagram." };

export function instagramCallbackNotice(outcome: string): InstagramCallbackNotice {
  return MESSAGES.get(outcome) ?? FALLBACK;
}

/** The callback outcome in the current URL, removing it from the address
 * bar; null when there is none. Browser-only — call after mount. */
export function takeInstagramCallbackOutcome(): string | null {
  const url = new URL(window.location.href);
  const outcome = url.searchParams.get(INSTAGRAM_CALLBACK_PARAM);
  if (outcome === null) return null;
  url.searchParams.delete(INSTAGRAM_CALLBACK_PARAM);
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  return outcome;
}
