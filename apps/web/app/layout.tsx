import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import { AnnouncerProvider } from "../components/Announcer";

import "./globals.css";

/**
 * The origin serving published artifacts, when it differs from the
 * page's own.
 *
 * Derived from the same build-time variable the loader reads, so the
 * policy and the fetches cannot disagree — a CSP naming one origin while
 * the loader fetches another fails at runtime in the browser, with an
 * error that points at the fetch rather than the policy.
 *
 * Reduced to an origin because CSP matches on origin, not path.
 */
const ARTIFACT_ORIGIN = (() => {
  const configured = process.env.NEXT_PUBLIC_ARTIFACT_BASE_URL;
  if (!configured) return "";
  try {
    return new URL(configured).origin;
  } catch {
    // A malformed value would otherwise produce a CSP that silently
    // blocks every artifact fetch. Empty means same-origin, which fails
    // loudly at the first request instead.
    return "";
  }
})();

export const metadata: Metadata = {
  title: "YouTube Creator Training Data Map",
  description:
    "Video identifiers observed in named, versioned dataset snapshots, " +
    "grouped by the country channels declare in their own YouTube metadata.",
  // Requirement 7.10 excludes creator detail from search indexes. The
  // overview is indexable; creator routes opt out individually.
  robots: { index: true, follow: true },
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <head>
        {/*
          Requirement 15.13 mandates a restrictive Content Security Policy.
          A static export has no server to set the header, so it is
          declared here.

          Two honest limitations, recorded rather than papered over:
          `frame-ancestors` and `report-uri` are ignored in a meta tag, so
          clickjacking protection needs a real header at the CDN. Task 8.2
          configures that; this is the strongest policy the static build
          can carry by itself.

          `'unsafe-inline'` for styles is required because Next injects
          critical CSS inline. Scripts get no such exemption.
        */}
        <meta
          httpEquiv="Content-Security-Policy"
          content={[
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            // MapLibre compiles its rendering workers from blobs.
            "worker-src 'self' blob:",
            // Artifacts may be served from a CDN origin rather than the
            // page's own. Adding exactly that origin — not a wildcard —
            // keeps the policy as tight as the deployment allows: a
            // `https:` here would permit exfiltration to anywhere.
            ARTIFACT_ORIGIN
              ? `connect-src 'self' ${ARTIFACT_ORIGIN}`
              : "connect-src 'self'",
            "font-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-src 'none'",
            "upgrade-insecure-requests",
          ].join("; ")}
        />
        <meta name="referrer" content="strict-origin-when-cross-origin" />
      </head>
      <body>
        {/* First tab stop, so keyboard users reach the data without
            traversing the header and filters (Requirement 13.2). */}
        <a className="skip-link" href="#main">
          Skip to main content
        </a>
        <AnnouncerProvider>
          <div className="shell">
            <header className="site-header">
              <div className="shell__inner">
                <h1 className="site-header__title">
                  YouTube Creator Training Data Map
                </h1>
                <p className="site-header__subtitle">
                  Video identifiers observed in named, versioned dataset
                  snapshots, grouped by the country channels declare in their
                  own YouTube metadata.
                </p>
                <nav className="site-nav" aria-label="Primary">
                  <Link href="/">Map</Link>
                  <Link href="/methodology">Methodology and limitations</Link>
                  <Link href="/methodology#corrections">Corrections</Link>
                </nav>
              </div>
            </header>

            <main id="main" className="shell__inner" tabIndex={-1}>
              {children}
            </main>

            <footer className="site-footer">
              <div className="shell__inner">
                {/* Requirement 12.9 keeps methodology reachable from every
                  view, including empty and error states. Putting it in the
                  footer guarantees that without each view remembering. */}
                <p>
                  This project reports observations, not conclusions. Dataset
                  membership means an identifier appeared in a documented
                  snapshot; it does not establish that a model was trained on a
                  video, that any use was lawful or unlawful, or that a creator
                  agreed to anything.{" "}
                  <Link href="/methodology">
                    Read the methodology and limitations
                  </Link>
                  .
                </p>
                <p>
                  Country shapes derived from Natural Earth (public domain).
                  Borders and names are a presentation convention, not a
                  position on any territorial question.
                </p>
              </div>
            </footer>
          </div>
        </AnnouncerProvider>
      </body>
    </html>
  );
}
