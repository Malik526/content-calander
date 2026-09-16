# Content Automation — Public Website

The public-facing website for Content Automation (Milestone 2.0.1) — a Next.js (App Router, TypeScript, Tailwind CSS v4) app, deployed as a static export. This is the beginning of the eventual SaaS frontend, not a throwaway compliance site: initial routes exist to provide real, stable public URLs for TikTok Developer Portal configuration, while the project structure (`app/`, `components/`, `lib/`) is meant to grow into the authenticated product UI later (`/app/calendar`, `/app/uploads`, etc. — not built yet).

This directory is intentionally separate from the Python backend/CLI tooling at the repository root (`process_content.py`, `content_store.py`, etc.) — see the root `README.md`/`PROJECT_STATE.md` for that side of the project.

## Routes

| Route | Purpose |
|---|---|
| `/` | Homepage — product positioning, how it works, current capabilities, platform roadmap, contact CTA. |
| `/privacy` | Privacy Policy. |
| `/terms` | Terms of Service. |

## Development

```bash
npm install
npm run dev      # http://localhost:3000
npm run lint
npm run build    # static export -> out/
```

## Design tokens

Colors and fonts are centralized in `app/globals.css`'s `:root`/`@theme` block — that's the one place to change the site's look (background, surface, ink/text, border, accent). Components reference these via Tailwind utilities (`bg-accent`, `text-ink-muted`, etc.), never a hardcoded hex value. Site-wide text/contact details (name, tagline, description, contact email) live in `lib/site-config.ts`.

**`lib/site-config.ts`'s `contactEmail` is a placeholder** (`support@content-automation.app`) — replace it with a real, monitored address before deploying this publicly; `/privacy` and `/terms` both reference it as a real contact method.

## Deployment (Netlify)

Deployed via the repository-root `netlify.toml` (`base = "web"`, static export from `out/`). No Netlify Next.js runtime plugin needed — there are no server-rendered/dynamic routes yet. See the root `README.md` for the exact steps and the resulting URLs needed for TikTok Sandbox configuration.

## Structure

```
app/                 routes (page.tsx per route, shared layout.tsx)
components/ui/       generic reusable primitives (Button, Container)
components/layout/   header/footer
components/sections/ homepage sections
lib/                 site-config.ts and future shared logic
```

Follows the Next.js App Router folder convention from this project's global engineering conventions (`components/ui`, `components/layout`, `components/sections`, `components/forms` when needed, `lib/`, `hooks/`, `types/`).
