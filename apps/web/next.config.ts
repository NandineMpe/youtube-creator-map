import type { NextConfig } from "next";

/**
 * GitHub Pages serves a project site from a subpath
 * (`/<repo>/`), so the app's own assets have to be requested under that
 * prefix or every chunk 404s. `NEXT_PUBLIC_BASE_PATH` supplies it at
 * build time; empty (the default) serves from the root, which is right
 * for local development and for a host that serves the app at its origin.
 *
 * This is the app's *own* asset prefix. It is unrelated to
 * `NEXT_PUBLIC_ARTIFACT_BASE_URL`, which points the data fetches at the
 * Supabase CDN — the two deployables live in different places on purpose.
 */
const basePath = process.env.NEXT_PUBLIC_BASE_PATH?.replace(/\/+$/, "") ?? "";

const nextConfig: NextConfig = {
  output: "export",
  poweredByHeader: false,
  reactStrictMode: true,
  basePath: basePath || undefined,
  // GitHub Pages has no trailing-slash rewriting, so `/methodology` must
  // resolve to `/methodology/index.html`. Trailing slashes make the
  // static export emit exactly that.
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
