import type { Metadata } from "next";
import { Container } from "@/components/ui/Container";
import { siteConfig } from "@/lib/site-config";

export const metadata: Metadata = {
  title: "Terms of Service",
  description: `The terms for using ${siteConfig.name}.`,
};

const LAST_UPDATED = "September 16, 2026";

export default function TermsPage() {
  return (
    <section className="py-16 sm:py-24">
      <Container>
        <div className="mx-auto max-w-2xl legal-content">
          <p className="text-sm font-medium text-accent">Legal</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-ink">Terms of Service</h1>
          <p className="mt-2 text-sm text-ink-muted">Last updated: {LAST_UPDATED}</p>

          <p className="mt-8">
            These terms govern your use of {siteConfig.name} (&ldquo;the Service&rdquo;). The
            Service is currently in active development and testing. By using it, you agree to
            these terms.
          </p>

          <h2>1. Acceptance of terms</h2>
          <p>
            By accessing or using the Service, you agree to be bound by these terms. If you do not
            agree, do not use the Service.
          </p>

          <h2>2. Description of the Service</h2>
          <p>
            The Service helps you organize finished videos into a posting schedule and automate
            parts of publishing that schedule to supported third-party platforms. Available
            features may change as the Service develops, and a feature described as
            &ldquo;in development&rdquo; is not yet guaranteed to be available.
          </p>

          <h2>3. Your content</h2>
          <p>
            You are solely responsible for the videos and other content you upload or connect to
            the Service, including ensuring you own it or otherwise have the rights necessary to
            use, publish, and distribute it. You represent that your content does not infringe any
            third party&rsquo;s rights and complies with applicable law and the terms of any
            platform it is published to.
          </p>

          <h2>4. Rights you grant us</h2>
          <p>
            You grant us the limited rights necessary to process, store, and transmit your content
            for the purpose of providing the Service to you — for example, generating a transcript
            or caption, or publishing a video to a platform you&rsquo;ve connected. We do not claim
            ownership of your content.
          </p>

          <h2>5. Third-party integrations</h2>
          <p>
            The Service integrates with third-party platforms (such as TikTok and Google) to
            provide scheduling and publishing functionality. Your use of those platforms through
            the Service is also subject to their own terms of service, and we are not responsible
            for their availability, behavior, or policies.
          </p>

          <h2>6. Experimental / beta service</h2>
          <p>
            The Service is an early-stage, experimental product. Features may change, break, or be
            removed without notice. You should not rely on the Service for time-sensitive or
            business-critical publishing until it has been explicitly designated stable.
          </p>

          <h2>7. No guarantee of availability</h2>
          <p>
            We do not guarantee uninterrupted or error-free operation of the Service, including
            scheduling accuracy or successful publishing to a connected platform. Use the Service
            with that limitation in mind.
          </p>

          <h2>8. Prohibited use</h2>
          <p>You agree not to use the Service to:</p>
          <ul>
            <li>Upload or publish content you do not have the right to distribute;</li>
            <li>Violate the terms of service of any connected third-party platform;</li>
            <li>Attempt to disrupt, reverse-engineer, or gain unauthorized access to the Service; or</li>
            <li>Use the Service for any unlawful purpose.</li>
          </ul>

          <h2>9. Disclaimer and limitation of liability</h2>
          <p>
            The Service is provided &ldquo;as is&rdquo; and &ldquo;as available,&rdquo; without
            warranties of any kind, express or implied. To the fullest extent permitted by law, we
            are not liable for any indirect, incidental, or consequential damages arising from your
            use of the Service, including content that fails to publish, is published incorrectly,
            or is lost.
          </p>

          <h2>10. Account and content deletion</h2>
          <p>
            You may request deletion of your account and associated data at any time by contacting{" "}
            <a href={`mailto:${siteConfig.contactEmail}`}>{siteConfig.contactEmail}</a>. We may also
            suspend or terminate access to the Service, particularly during this testing phase, if
            needed to maintain or improve it.
          </p>

          <h2>11. Changes to these terms</h2>
          <p>
            We may update these terms as the Service develops. Continued use of the Service after a
            change means you accept the updated terms.
          </p>

          <h2>12. Contact</h2>
          <p>
            Questions about these terms can be sent to{" "}
            <a href={`mailto:${siteConfig.contactEmail}`}>{siteConfig.contactEmail}</a>.
          </p>
        </div>
      </Container>
    </section>
  );
}
