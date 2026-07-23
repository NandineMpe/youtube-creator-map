# YouTube Creator Training Data Map

Greenfield monorepo for a metadata-only batch pipeline and static public application. The project
does not download videos, transcripts, thumbnails, or dataset media.

**Live:** https://youtube-creator-map.vercel.app/ (Vercel) · https://nandinempe.github.io/youtube-creator-map/ (GitHub Pages)
**Data CDN:** https://ffipewnioaxjfhieqbaw.supabase.co/storage/v1/object/public/creator-map

Two working deployments of the same app. Vercel serves the site at its origin (no base path) and
delivers the security headers — including `frame-ancestors` and HSTS — as real response headers via
`vercel.json`. GitHub Pages serves it from a project subpath with the same policy in a `<meta>` tag.
Both fetch data from the Supabase CDN.

The deployment is split across two hosts on purpose. The release artifacts and the app's hashed
JS/CSS live on Supabase Storage, which serves JSON and JavaScript with correct content types. The
HTML entry point lives on GitHub Pages, because Supabase Storage forces `text/plain` on uploaded
HTML as an anti-XSS measure and so cannot serve a browsable page. The app is built with the data
CDN origin baked in (`NEXT_PUBLIC_ARTIFACT_BASE_URL`) and that exact origin added to its
`connect-src` CSP; the two deployables never talk to a host they were not built for.

Deploy both halves with `scripts/publish.ps1` (Supabase data) and `scripts/deploy_pages.ps1`
(GitHub Pages app).

## Workspace boundaries

- `python/packages/schemas`: versioned contracts; imports neither infrastructure nor orchestration.
- `python/packages/restricted-infra`: restricted storage, network, identity, and secret adapters; may
  depend on schemas but never the pipeline.
- `python/packages/pipeline`: Dagster orchestration and metadata processing; may depend inward on
  schemas and restricted infrastructure.
- `packages/shared-schemas`: public-safe TypeScript artifact contracts; never imports app or
  restricted code.
- `apps/web`: static Next.js public app; depends only on public-safe TypeScript packages.

Python boundaries are checked by import-linter and unit tests. TypeScript boundaries are checked by
ESLint restricted-import rules and workspace dependency declarations.

## Bootstrap and validation

Use Python 3.11 and Node 22 on Windows, the target platform recorded by the Python lock. Install
Python and npm dependencies from their lockfiles, then install the local distributions without
re-resolving dependencies:

```text
python -m pip install -r pylock.toml
python -m pip install --no-deps -e python/packages/schemas -e python/packages/restricted-infra -e python/packages/pipeline
npm ci
```

Run `npm run format:check`, `npm run lint`, `npm run typecheck`, `npm test`, and `npm run build`.
`npm run ci` regenerates the platform-specific Python lock to a temporary file, compares it with the
committed lock, verifies npm lock integrity, and runs known-vulnerability scans. Direct dependency
license review is recorded in `THIRD_PARTY_LICENSES.md`; security policy is summarized in
`SECURITY.md`.

## Building and publishing a release

A release is built, validated, and only then activated. The steps are separate on purpose: a release
that fails a gate is a build that stopped, not an outage, because the previously active release keeps
serving (Requirement 8.3).

```text
# 1. Build artifacts, run every gate, compute the delivery plan. Activates nothing.
$env:PUBLIC_KEY_SECRET = "<secret>"
pwsh scripts/build_release.ps1 -Actor <you>

# 2. Review the artifacts, then record sign-off. Scoped to this manifest
#    digest: rebuild with different numbers and the approval no longer applies.
python -m creator_map_pipeline.cli_release signoff --dir dist --actor <curator> --citations --terms

# 3. Full acceptance: every gate plus the test, type, lint, and scan suites.
python -m creator_map_pipeline.cli_release accept --dir dist --actor <you> --scan-completed --json dist/acceptance-report.json

# 4. Activate. Separate command, separate decision.
python -m creator_map_pipeline.cli_release activate --dir dist --actor <you> --scan-completed
```

`cli_release rollback --to <release-id>` restores a prior release through one atomic pointer change,
after re-verifying that release's manifest and every artifact digest.

## Publishing to a CDN (Supabase Storage)

The site is a static export that reads published JSON, so both the release
artifacts and the web bundle have to live on an object store the browser
can reach. `cli_publish` uploads them to Supabase Storage; the app fetches
them from the public bucket URL baked in at build time.

```text
# The public config lives in .env.local (checked as ignored). The service
# role key does not — it is a full-access credential, set per session:
$env:SUPABASE_SERVICE_KEY = '<service_role key from Project Settings > API>'

# Build the bundle for the CDN and publish, moving the pointer last:
pwsh scripts/publish.ps1

# Or stage without going live, to review first:
pwsh scripts/publish.ps1 -StageOnly
```

The publishable key cannot write to storage by design; a publish attempt
with it fails on a row-level-security policy rather than doing anything.
Artifacts upload before the pointer, and the pointer only if every
artifact landed (Requirement 8.7). After uploading, the publisher re-reads
each object to confirm it is served at the right URL under the right
cache headers, rather than trusting that a 200 on upload means the bytes
are correct.

The CDN origin is added to the site's `connect-src` CSP at build time —
exactly that origin, not a wildcard — so a bundle built for one deployment
will not silently talk to another.

### Delivery layout

`cli_deliver` computes the object-storage plan without needing a storage credential, so it can run in
CI and be reviewed before anything is uploaded. Two caching classes, and the difference matters:

- `releases/<release-id>/…` — immutable, cached for a year. Safe only because the release id is in
  the path, so a given URL's bytes never change.
- `active-release.json` — one stable URL whose contents change on every activation, cached for 60
  seconds. Caching this like an artifact would leave clients on the old release after a rollback.

Artifacts are published before the pointer. A pointer naming a release whose shards have not landed
would leave clients with no complete release to fall back to (Requirement 8.7).

Security response headers live in `python/…/release/headers.py` rather than only in a `<meta>` tag,
because `frame-ancestors` is ignored in a meta tag and `Strict-Transport-Security` has no meta
equivalent. `deploy/headers.json` and `apps/web/public/_headers` are generated from it.

## What this reports, and what it does not

The map summarizes YouTube video identifiers observed in AI-training dataset source materials,
grouped by the country declared in YouTube channel metadata. Those are observations about dataset
contents and channel metadata. They are not claims about whether any model was trained on a video,
about copyright status, legality, or consent, and a declared country is not a claim about anyone's
residence or nationality.
