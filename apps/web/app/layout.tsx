import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import "./globals.css";

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
      <body>
        {/* First tab stop, so keyboard users reach the data without
            traversing the header and filters (Requirement 13.2). */}
        <a className="skip-link" href="#main">
          Skip to main content
        </a>
        <div className="shell">
          <header className="site-header">
            <div className="shell__inner">
              <h1 className="site-header__title">
                YouTube Creator Training Data Map
              </h1>
              <p className="site-header__subtitle">
                Video identifiers observed in named, versioned dataset
                snapshots, grouped by the country channels declare in their own
                YouTube metadata.
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
                Borders and names are a presentation convention, not a position
                on any territorial question.
              </p>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
