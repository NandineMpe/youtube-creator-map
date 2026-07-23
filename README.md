# YouTube Creator Training Data Map

Greenfield monorepo for a metadata-only batch pipeline and static public application. The project
does not download videos, transcripts, thumbnails, or dataset media.

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
