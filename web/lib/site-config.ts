/**
 * site-config.ts — single source of truth for site-wide text/contact
 * details used across layout, metadata, and the privacy/terms pages.
 *
 * contactEmail is a PLACEHOLDER — replace it with a real, monitored
 * address before this site is deployed publicly or submitted anywhere
 * (including TikTok Developer Portal review), since /privacy and /terms
 * both promise it as a real contact method.
 */
export const siteConfig = {
  name: "Content Automation",
  tagline: "Turn finished videos into a running posting schedule.",
  description:
    "Content Automation helps creators batch their finished videos, organize them into a posting schedule, and automate the repetitive work between creating content and publishing it.",
  url: "https://contentautomation.app",
  contactEmail: "support@content-automation.app",
} as const;
