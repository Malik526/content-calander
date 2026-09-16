import type { Metadata } from "next";
import { Container } from "@/components/ui/Container";
import { siteConfig } from "@/lib/site-config";

export const metadata: Metadata = {
  title: "Privacy Policy",
  description: `How ${siteConfig.name} handles your data.`,
};

const LAST_UPDATED = "September 16, 2026";

export default function PrivacyPage() {
  return (
    <section className="py-16 sm:py-24">
      <Container>
        <div className="mx-auto max-w-2xl legal-content">
          <p className="text-sm font-medium text-accent">Legal</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-ink">Privacy Policy</h1>
          <p className="mt-2 text-sm text-ink-muted">Last updated: {LAST_UPDATED}</p>

          <p className="mt-8">
            {siteConfig.name} is currently under active development and testing. This policy
            describes how the product handles data today. It will be updated as the product
            changes, and material changes will be reflected in the &ldquo;Last updated&rdquo; date
            above.
          </p>

          <h2>What Content Automation is</h2>
          <p>
            Content Automation is a tool that helps creators organize finished videos into a
            posting schedule and automate parts of publishing that schedule to third-party
            platforms such as TikTok. This policy covers the information involved in providing
            that functionality.
          </p>

          <h2>Information we access when you connect a third-party service</h2>
          <p>
            To publish on your behalf or manage a schedule for you, Content Automation may need to
            connect to third-party services such as TikTok or Google Calendar. When you connect an
            account, we may access basic account information (such as an account identifier) and
            request permission scopes needed for the specific feature — for example, permission to
            publish a video, or to create and manage events on a dedicated calendar. We request
            only the access needed for the features you use.
          </p>

          <h2>How access tokens are handled</h2>
          <p>
            When you authorize an integration, the resulting access and refresh tokens are stored
            securely and used only to operate that integration on your behalf (for example, to
            publish a video you&rsquo;ve queued, or to keep a schedule calendar up to date). Tokens
            are not shared with unrelated third parties.
          </p>

          <h2>Video content and metadata</h2>
          <p>
            Videos you provide, along with metadata about them (such as duration, format, and
            scheduling status), are processed in order to provide scheduling and publishing
            functionality. This may include generating a transcript and a caption candidate from a
            video&rsquo;s audio, which are stored alongside the video&rsquo;s record so they can be
            reviewed and used when the video is published.
          </p>

          <h2>What we do not do</h2>
          <ul>
            <li>We do not sell your data.</li>
            <li>
              We do not use your video content or transcripts for any purpose beyond providing the
              scheduling/publishing functionality you&rsquo;ve requested.
            </li>
          </ul>

          <h2>Third-party services</h2>
          <p>
            Connected third-party platforms (such as TikTok and Google) process data under their
            own privacy policies once information is sent to or received from them. We encourage
            you to review{" "}
            <a href="https://www.tiktok.com/legal/privacy-policy" target="_blank" rel="noreferrer">
              TikTok&rsquo;s Privacy Policy
            </a>{" "}
            and{" "}
            <a href="https://policies.google.com/privacy" target="_blank" rel="noreferrer">
              Google&rsquo;s Privacy Policy
            </a>{" "}
            for how they handle information on their end.
          </p>

          <h2>Data deletion and contact</h2>
          <p>
            You can request deletion of your account data, including stored tokens, transcripts,
            and video records, at any time by contacting us at{" "}
            <a href={`mailto:${siteConfig.contactEmail}`}>{siteConfig.contactEmail}</a>. Because the
            product is in active development, deletion requests are currently handled manually and
            we will confirm once a request has been completed.
          </p>

          <h2>Changes to this policy</h2>
          <p>
            As Content Automation moves from testing toward a broader release, this policy will be
            revised to reflect new functionality (such as additional publishing platforms or
            account features). We&rsquo;ll update the date at the top of this page when that
            happens.
          </p>

          <h2>Contact</h2>
          <p>
            Questions about this policy or your data can be sent to{" "}
            <a href={`mailto:${siteConfig.contactEmail}`}>{siteConfig.contactEmail}</a>.
          </p>
        </div>
      </Container>
    </section>
  );
}
