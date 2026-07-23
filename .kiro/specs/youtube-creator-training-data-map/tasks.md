# Implementation Plan: YouTube Creator Training Data Map

## Overview

Implement the approved greenfield design as a Python/Dagster/PostgreSQL/DuckDB batch pipeline and a TypeScript/React/Next.js static public application. Work proceeds in eight gated increments of five core implementation leaves. Each mandatory checkpoint rereads `design.md` and `requirements.md`, validates all completed work, and fixes drift before downstream waves may begin.

## Tasks

- [ ] 1. Establish the typed, reproducible, security-aware foundation
  - [x] 1.1 Scaffold the Python pipeline and TypeScript web workspaces
    - Configure pinned runtime/development dependencies, lockfiles, formatting, linting, type checking, unit-test commands, and CI entry points without adding media-download dependencies.
    - Keep pipeline, restricted infrastructure, shared schemas, and public web packages separated by explicit module boundaries.
    - _Requirements: 15.12, 15.14, 15.15_
  - [x] 1.2 Implement versioned Python domain contracts and validation models
    - Define dataset, occurrence, resolution, work-item, quota, filter, aggregate, disclosure-policy, and release-manifest types with fail-closed validation and deterministic serialization.
    - Encode statuses, country/unknown semantics, cutoff policies, and required provenance fields from the approved design.
    - _Requirements: 1.1, 1.2, 2.7-2.10, 3.5-3.12, 4.1, 5.1-5.13, 6.1-6.6, 8.1_
  - [x] 1.3 Create PostgreSQL migrations and repository boundaries
    - Add append-only provenance and observation tables, dataset-video joins, unique work identities, leases, checkpoints, quota ledger, policy records, suppressions, release records, and durable audit logs.
    - Add constraints and indexes that enforce immutable contracts, idempotent work, transactional checkpoints, deterministic observation selection, and role/resource separation.
    - _Requirements: 1.3, 1.4, 1.8, 3.1-3.4, 3.9-3.12, 4.1-4.7, 15.4-15.6, 15.20-15.22_
  - [x] 1.4 Implement restricted infrastructure ports and security-policy configuration
    - Define managed-secret, workload-identity, encrypted storage, egress allowlist, authorization, operation-rate-limit, audit, alert, and S3-compatible object-store interfaces with secure defaults.
    - Ensure credentials and restricted values cannot enter persistent domain state, logs, or public interfaces.
    - _Requirements: 15.1-15.6, 15.10-15.13, 15.16-15.22_
  - [x] 1.5 Define versioned public artifact schemas and generated TypeScript consumers
    - Specify JSON/GeoJSON schemas for active pointers, manifests, country summaries, coverage, country details, creator pages, methodology, and recoverable error metadata.
    - Generate or validate matching TypeScript types while excluding raw video IDs, source locators, raw responses, contacts, and restricted joins.
    - _Requirements: 7.2-7.7, 8.1, 8.8, 9.1-9.5, 10.2-10.7, 14.4, 14.5, 15.3_
  - [x] 1.6 Checkpoint — validate foundation alignment before ingestion work
    - Reread `design.md` and `requirements.md`; inspect tasks 1.1-1.5 against their cited clauses, run available lockfile, schema, migration, lint, type, and security checks, and correct every discrepancy or regression before proceeding.
    - Ensure all tests pass, ask the user if questions arise.
  - [x]* 1.7 Write unit and migration tests for foundation contracts
    - Test required-field failures, deterministic serialization, database constraints, rollback behavior, restricted/public schema separation, and secrets/log redaction.
    - _Requirements: 1.1-1.8, 3.9-3.12, 4.1-4.7, 7.5-7.7, 15.1-15.6, 15.20-15.22_

- [ ] 2. Implement governed ingestion and exact extraction accounting
  - [x] 2.1 Implement the immutable dataset registry and approval gate
    - Add idempotent registration, conflicting-revision rejection, completeness/access review, version-key enforcement, preserved prior contracts, and methodology-facing approved metadata export.
    - _Requirements: 1.1-1.5, 1.8, 1.9_
  - [x] 2.2 Implement canonical YouTube video-ID parsing and printing
    - Parse only the versioned supported bare-ID and URL grammar, return stable non-empty rejection reasons, and provide a pure canonical bare-ID printer.
    - Treat all input as untrusted and enforce configured length/shape limits before parsing.
    - _Requirements: 2.1-2.6, 15.7-15.9_
  - [x] 2.3 Implement digest-gated, bounded snapshot readers
    - Verify approved contract and snapshot digest before extraction; enforce schema, field/record size, path traversal, formula non-execution, and archive-decompression limits.
    - Quarantine violations with non-sensitive provenance and fail closed on digest mismatch or schema drift without disturbing prior outputs.
    - _Requirements: 1.5-1.7, 2.15, 2.16, 15.7-15.9_
  - [x] 2.4 Implement source-adapter extraction and reporting framework
    - Provide source-kind adapter interfaces and fixture adapters that retain repeated evidence, validate clip bounds, preserve locators, handle incomplete provenance, and account accepted/rejected/expanded records exactly.
    - Persist completed outputs atomically and require versioned adapters for changed schemas.
    - _Requirements: 2.7-2.16_
  - [x] 2.5 Wire governed ingestion as partitioned Dagster assets and curator CLI commands
    - Register contracts, validate pinned snapshots, run adapters, write immutable Parquet partitions and PostgreSQL provenance, and emit extraction reports through authenticated, authorized, rate-limited, audited operations.
    - Restrict egress to approved object storage and ensure metadata-only processing never retrieves media.
    - _Requirements: 1.1-1.9, 2.7-2.16, 15.10-15.12, 15.16-15.21_
  - [x] 2.6 Checkpoint — validate ingestion alignment before enrichment work
    - Reread `design.md` and `requirements.md`; audit tasks 2.1-2.5 and prior work for provenance, conservation, fail-closed behavior, and security boundaries; run targeted ingestion, migration, lint, type, and security validation and correct all drift before proceeding.
    - Ensure all tests pass, ask the user if questions arise.
  - [x]* 2.7 Write adapter contract and golden-fixture integration tests
    - Cover approved/mismatched digests, bare IDs and URL variants, malformed data, repeated clips, expansion accounting, incomplete provenance, schema drift, archive/path/formula attacks, and immutable prior outputs.
    - _Requirements: 1.3-1.8, 2.1-2.16, 15.7-15.9_
  - [x]* 2.8 Write property test for normalization idempotence
    - **Property 1: Normalization Idempotence** — generate supported and rejected forms and prove canonical successes normalize to themselves with deterministic outcomes.
    - **Validates: Requirements 2.2, 2.3**
  - [x]* 2.9 Write property test for occurrence conservation
    - **Property 2: Occurrence Conservation** — generate source records and expansion rules and prove accepted plus rejected records equal examined records and emitted counts equal reported expansion totals.
    - **Validates: Requirements 2.12-2.14**

- [ ] 3. Implement cached, resumable, quota-safe enrichment
  - [x] 3.1 Implement distinct work planning, append-only observation caching, and cutoff selection
    - Plan one video/channel work item per entity and policy, preserve dataset membership separately, reuse eligible cache entries, and select one observation deterministically at a pinned cutoff.
    - _Requirements: 3.1-3.4, 3.9-3.12, 4.1_
  - [x] 3.2 Implement transactional leases and idempotent batch checkpoints
    - Claim at most 50 distinct eligible items for one authenticated worker, reclaim expired leases, and atomically commit outcomes, state transitions, response digests, and quota usage with replay safety.
    - _Requirements: 4.2-4.7_
  - [x] 3.3 Implement the minimal-field YouTube metadata client
    - Resolve video-to-channel and channel display/country observations in bounded batches, classify omitted IDs as unavailable-unclassified, normalize only declared country, and append response-digested observations.
    - Request no media, transcript, thumbnail, contact, or unnecessary metadata and enforce approved endpoint egress.
    - _Requirements: 3.5-3.9, 4.18, 15.10-15.12_
  - [x] 3.4 Implement retry, quota-reserve, and operator-halt state machines
    - Add bounded exponential backoff with jitter, attempt-terminal transitions, zero-cost claim handling, quota projection, credential/policy halts, alerts, and authorized recovery scopes.
    - _Requirements: 4.8-4.17_
  - [x] 3.5 Wire video and channel enrichment assets plus secured operator commands
    - Compose planning, caching, leasing, API batches, checkpoints, resume, quota reset, halt recovery, and summaries in Dagster and the curator CLI.
    - Require authentication, authorization, operation-specific rate limiting, durable auditing, and rollback/denial when audit writes fail.
    - _Requirements: 3.1-3.12, 4.1-4.18, 15.16-15.22_
  - [x] 3.6 Checkpoint — validate enrichment alignment before aggregation work
    - Reread `design.md` and `requirements.md`; verify tasks 3.1-3.5 and prior work preserve append-only history, exact work uniqueness, lease/checkpoint semantics, quota reserve, no inferred country, minimal fields, and administrative controls; run targeted validation and fix drift before proceeding.
    - Ensure all tests pass, ask the user if questions arise.
  - [x]* 3.7 Write fake-API and PostgreSQL enrichment integration tests
    - Cover 0/1/50 IDs, omitted IDs, cache reuse, response drift, concurrent/expired leases, checkpoint rollback/replay, retry headers, quota boundaries, invalid credentials, operator recovery, and interrupted restarts.
    - _Requirements: 3.1-3.12, 4.1-4.18, 15.10-15.12, 15.16-15.21_
  - [x]* 3.8 Write property test for no inferred country
    - **Property 6: No Inferred Country** — generate channel metadata with absent/unsupported country plus arbitrary other fields and prove the selected country is always Unknown.
    - **Validates: Requirements 3.8, 5.7**
  - [x]* 3.9 Write property test for resumption equivalence
    - **Property 10: Resumption Equivalence** — generate interruption, lease-expiry, checkpoint-replay, and retry schedules and prove the committed observation set equals uninterrupted execution for fixed observations and cutoff.
    - **Validates: Requirements 4.4-4.7, 4.17**

- [ ] 4. Build exact aggregates and enforce the publication boundary
  - [x] 4.1 Implement deterministic DuckDB filter and distinct-set aggregation
    - Build exact occurrence, represented-video, creator, country, and per-dataset counts from pinned Parquet/observation inputs with dataset and corpus-class isolation and deterministic ordering.
    - Preserve cross-dataset membership while deduplicating only at declared count boundaries.
    - _Requirements: 5.1-5.5, 5.8-5.13_
  - [x] 4.2 Implement resolution coverage and Unknown Country aggregation
    - Partition every distinct filtered video into exactly one resolution state, reconcile channel country coverage, and retain unresolved and unknown totals without contributing unresolved videos to creator/country counts.
    - _Requirements: 5.5-5.7, 6.1-6.6_
  - [x] 4.3 Implement creator summaries, deterministic detail sorting, and cursor pages
    - Compute distinct represented videos and per-dataset breakdowns per channel, deterministic tie-breakers/cursors, configured page sizes, and complete exactly-once traversal.
    - _Requirements: 5.3-5.5, 10.2-10.8, 14.5_
  - [x] 4.4 Implement the versioned disclosure-policy engine
    - Fail closed for missing/invalid policy, apply creator and field rules plus corrections/opt-outs/suppressions, derive public channel keys, and expose no suppression reason.
    - Ensure uncertain permissions are prohibited and aggregate behavior follows the pinned policy.
    - _Requirements: 7.1-7.4, 7.7, 7.8_
  - [x] 4.5 Generate disclosure-reviewed public aggregate and detail artifacts
    - Serialize schemas from task 1.5, recursively inspect keys/values/metadata/indexes, reject prohibited or restricted content, omit suppressed identities from every public surface, and emit methodology/coverage context.
    - _Requirements: 6.7-6.11, 7.2-7.7, 7.9-7.11, 10.3-10.7, 12.1-12.11_
  - [ ] 4.6 Checkpoint — validate aggregation and disclosure alignment before release work
    - Reread `design.md` and `requirements.md`; reconcile tasks 4.1-4.5 against exact-set, coverage, detail-page, disclosure, and publication-boundary clauses; run aggregate/schema/privacy validation and correct all drift before proceeding.
    - Ensure all tests pass, ask the user if questions arise.
  - [x]* 4.7 Write aggregate, pagination, disclosure, and artifact unit tests
    - Cover duplicate evidence, overlap, empty filters, all partition states, unsupported countries, stable sorting/cursors, threshold edges, suppressions, recursive prohibited fields, and deterministic bytes.
    - _Requirements: 5.1-5.13, 6.1-6.6, 7.1-7.8, 10.5-10.7_
  - [x]* 4.8 Write property test for within-dataset deduplication
    - **Property 3: Within-Dataset Deduplication** — prove added duplicate occurrences change occurrence counts but never distinct represented-video counts.
    - **Validates: Requirements 5.1, 5.2**
  - [x]* 4.9 Write property test for cross-dataset union semantics
    - **Property 4: Cross-Dataset Union Semantics** — prove combined video sets are exact unions and overlaps count once combined but once in each applicable breakdown.
    - **Validates: Requirements 5.3, 5.12**
  - [x]* 4.10 Write property test for creator attribution uniqueness
    - **Property 5: Creator Attribution Uniqueness** — prove each resolved video contributes to at most one channel, country bucket, and creator represented-video count.
    - **Validates: Requirements 5.4-5.6**
  - [x]* 4.11 Write property test for coverage partition
    - **Property 7: Coverage Partition** — prove generated resolution states are disjoint and exhaustive and channel country partitions reconcile.
    - **Validates: Requirements 6.1-6.6**
  - [x]* 4.12 Write property test for filter isolation
    - **Property 8: Filter Isolation** — generate dataset/corpus filters and prove excluded occurrences cannot affect any count.
    - **Validates: Requirements 5.8, 5.9**
  - [x]* 4.13 Write property test for monotonic union of resolved identities
    - **Property 9: Monotonic Union of Resolved Identities** — prove subset filter video sets and resolved creator/video totals cannot exceed their supersets.
    - **Validates: Requirements 5.10, 5.11**
  - [x]* 4.14 Write property test for disclosure noninterference
    - **Property 13: Disclosure Noninterference** — recursively inspect generated artifacts, indexes, telemetry, logs, errors, and payload fixtures and prove suppressed identities never appear.
    - **Validates: Requirements 7.2-7.7, 7.10, 7.11**

- [ ] 5. Validate, stage, activate, and roll back immutable releases
  - [ ] 5.1 Implement release-candidate assembly and digest manifests
    - Pin snapshots, cutoff, filters, methodology/policy versions, generation time, complete artifact inventory, cryptographic digests, and immutable cache metadata in deterministic manifests.
    - _Requirements: 8.1, 14.10_
  - [ ] 5.2 Implement composable release validation gates and reports
    - Gate arithmetic, provenance, disclosure, neutral language, schemas, digests, accessibility smoke, citations/terms, payload size, lockfiles/licenses/vulnerabilities, secret scans, and required security policy checks.
    - Fail closed on failed or incomplete gates and report each internal result without changing the active release.
    - _Requirements: 8.2, 8.3, 12.12, 12.13, 14.1, 15.3, 15.14, 15.15_
  - [ ] 5.3 Implement versioned staging and full artifact verification
    - Stage a complete candidate in object storage, recompute every digest, reject missing/mismatched artifacts, and serve only manifest-matching immutable files.
    - _Requirements: 8.4-8.6, 8.8, 14.10_
  - [ ] 5.4 Implement atomic activation, verified rollback, and historical immutability
    - Swap only the separately refreshable active pointer after full verification, verify rollback targets before one-step restoration, and prevent later inputs from mutating prior release artifacts/counts.
    - _Requirements: 8.7-8.12, 14.9-14.11_
  - [ ] 5.5 Wire the release asset graph and secured release CLI
    - Build aggregate/detail/methodology artifacts, run every gate, stage, activate, invalidate policy-affected shards, and roll back through authenticated, authorized, rate-limited, durably audited commands.
    - Keep the previous verified release active on every failure path.
    - _Requirements: 7.8, 8.1-8.12, 14.9-14.11, 15.16-15.22_
  - [ ] 5.6 Checkpoint — validate release alignment before public-app work
    - Reread `design.md` and `requirements.md`; inspect tasks 5.1-5.5 and prior outputs for complete gates, immutable artifacts, atomicity, rollback, cache policy, and administrative security; execute synthetic release validation and correct drift before proceeding.
    - Ensure all tests pass, ask the user if questions arise.
  - [ ]* 5.7 Write synthetic release contract and failure-path integration tests
    - Build a complete release; verify counts, schemas, digests, security/privacy/language gates, missing/corrupt artifacts, policy invalidation, activation failure, rollback failure/success, and prior-release availability.
    - _Requirements: 7.8, 8.1-8.12, 12.12, 14.1, 14.9-14.11, 15.3, 15.14, 15.15_
  - [ ]* 5.8 Write property test for historical immutability
    - **Property 11: Historical Immutability** — generate later snapshots/observations and prove published manifest bytes, artifact digests, and counts remain unchanged.
    - **Validates: Requirements 8.9**
  - [ ]* 5.9 Write property test for atomic publication
    - **Property 15: Atomic Publication** — generate staging, verification, activation, and rollback failures and prove observers see only the complete previous or complete new verified release.
    - **Validates: Requirements 8.4-8.8, 8.10-8.12**

- [ ] 6. Build the public overview, verified loader, filters, map, and table
  - [ ] 6.1 Implement the static Next.js application shell and approved methodology content
    - Create the dark responsive shell, semantic layout, headline placeholders, global methodology/limitations access, correction path, dataset citations, count definitions, release/cutoff context, and boundary attribution/license conventions.
    - Use approved observational language and distinguish comparison corpora without legal or moral implications.
    - _Requirements: 1.9, 7.9, 9.1, 9.8, 12.1-12.11, 13.1, 13.2_
  - [ ] 6.2 Implement the verified release/artifact loader and resilient cache state
    - Load the active pointer and manifest, validate schemas/digests, prohibit mixed releases, retry missing/corrupt shards, preserve the last verified state, and distinguish loading, empty, delivery, digest, and refresh failures.
    - _Requirements: 8.8, 10.10, 14.6-14.11_
  - [ ] 6.3 Implement the canonical URL codec and exploration state reducer
    - Parse/serialize release, ordered filter sets, country/Unknown, metric, page cursor, and sort using deterministic defaults and a versioned fallback policy with corrected-component reporting.
    - Keep failed updates on the preceding valid state.
    - _Requirements: 9.6, 9.10-9.12, 10.8-10.10, 11.1-11.10_
  - [ ] 6.4 Implement headline metrics, filter controls, coverage, and state presentations
    - Render active filter/release/cutoff, exact count units, full resolution partitions, comparison labels, Unknown Country card, zero-input Empty State, loading states, and recoverable errors from verified artifacts.
    - Update all overview surfaces and URL atomically on filter changes.
    - _Requirements: 6.7-6.11, 9.1, 9.6-9.8, 9.12, 12.3, 12.4, 12.7-12.9_
  - [ ] 6.5 Implement MapLibre choropleth and authoritative sortable country table
    - Derive map and table from the same summaries; implement exact quantile/log bins, no-data styling, pattern/text equivalents, hover/focus parity, metric switching, Unknown outside geography, and preserved selection across views.
    - Include versioned Natural Earth-derived boundaries and metadata without location inference.
    - _Requirements: 6.8, 9.2-9.5, 9.7, 9.9-9.11, 12.10, 12.11, 13.4, 13.8_
  - [ ] 6.6 Checkpoint — validate public overview alignment before detail work
    - Reread `design.md` and `requirements.md`; compare tasks 6.1-6.5 and prior artifacts with loader, URL, overview, filter, coverage, map/table, neutral-copy, and error-state clauses; run web lint/type/unit/build/schema checks and correct drift before proceeding.
    - Ensure all tests pass, ask the user if questions arise.
  - [ ]* 6.7 Write web unit and component tests for loading, URL, filters, bins, and parity
    - Cover canonical round trips/fallbacks, reducer rollback, exact legends, number formatting, map/table identity, empty/error distinctions, Unknown Country, comparison labels, digest retries, and mixed-release prevention.
    - _Requirements: 6.7-6.11, 9.1-9.12, 11.1-11.10, 14.6-14.11_
  - [ ]* 6.8 Write property test for neutral labeling
    - **Property 14: Neutral Labeling** — generate public views and recursively lint rendered/exported copy so membership never becomes a claim about training, legality, infringement, consent, residence, or nationality.
    - **Validates: Requirements 12.1-12.8, 12.12, 12.13**

- [ ] 7. Implement consistent country details, accessibility, responsiveness, and browser privacy
  - [ ] 7.1 Implement verified country-detail selection and recovery
    - Synchronize click, keyboard, and table actions with heading, map/table selection, URL, aggregate totals, dataset breakdown, coverage, release/cutoff, and lazy detail loading.
    - Preserve valid context and never substitute zero, stale, partial, or Empty State data for a failed detail.
    - _Requirements: 10.1-10.4, 10.9-10.11, 12.8, 12.9, 14.4, 14.6-14.8_
  - [ ] 7.2 Implement deterministic creator sorting and pagination UI
    - Render only disclosure-approved fields, stable cursor pages and sort state, exactly-once traversal, canonical URLs, long-name handling, and valid no-publishable-creator Empty States.
    - _Requirements: 7.2-7.4, 10.4-10.8, 10.11_
  - [ ] 7.3 Implement semantic interaction and accessible status infrastructure
    - Add semantic headings/landmarks, labeled controls, visible focus, logical focus restoration, live filter/loading/completion announcements, status/error relationships, and Enter/Space parity with pointer actions.
    - Ensure the country table is the complete keyboard/screen-reader equivalent of map discovery.
    - _Requirements: 9.5, 9.9, 13.1-13.5, 13.8, 13.9, 13.11_
  - [ ] 7.4 Implement responsive detail presentation and user-preference styles
    - Provide equivalent desktop side panel and small-screen bottom sheet controls/content, WCAG AA contrast, non-color patterns/text, reduced-motion behavior, touch targets, reflow, and 200% zoom support without two-dimensional scrolling.
    - _Requirements: 10.12, 13.1, 13.6, 13.7, 13.10_
  - [ ] 7.5 Implement public privacy, SEO, telemetry, and browser-security controls
    - Exclude creator details from indexing/sitemaps, emit only optional coarse policy-approved telemetry, apply restrictive CSP/HSTS/SRI as applicable, and prevent anonymous mutation or secret-bearing browser configuration.
    - Keep methodology and correction/opt-out access available from details, empty states, and errors.
    - _Requirements: 7.3, 7.9-7.11, 12.9, 15.3, 15.13, 15.19_
  - [ ] 7.6 Checkpoint — validate detail, accessibility, and privacy alignment before final integration
    - Reread `design.md` and `requirements.md`; evaluate tasks 7.1-7.5 and prior UI for detail consistency, pagination, all interaction paths, WCAG 2.2 AA, mobile/reduced-motion/zoom behavior, SEO/telemetry minimization, and browser security; run relevant validation and fix drift before proceeding.
    - Ensure all tests pass, ask the user if questions arise.
  - [ ]* 7.7 Write country-detail and interaction integration tests
    - Cover map/table/pointer/keyboard equivalence, summary-detail parity, URL synchronization, lazy loading, stable pages, disclosure-only rows, empty detail, corrupt shard recovery, and desktop/mobile functional equivalence.
    - _Requirements: 10.1-10.12, 13.4, 13.5, 13.10, 13.11, 14.4, 14.6-14.8_
  - [ ]* 7.8 Write property test for country-detail consistency
    - **Property 12: Country-Detail Consistency** — generate releases, filters, and country selections and prove panel creator/video totals equal the corresponding map/table summary totals.
    - **Validates: Requirements 10.1, 10.2**

- [ ] 8. Complete cross-stack integration, operational validation, performance, and release readiness
  - [ ] 8.1 Implement deterministic synthetic fixtures and a cross-stack contract harness
    - Generate permitted source snapshots, API observations, policies, suppressions, release inputs, boundary metadata, and expected reconciliations usable by Python, TypeScript, and browser validation without real restricted data.
    - _Requirements: 2.12-2.16, 3.5-3.12, 5.1-5.13, 6.1-6.6, 7.1-7.7, 8.1_
  - [ ] 8.2 Wire the full pipeline to the static web build and versioned delivery layout
    - Connect ingestion, enrichment, aggregation, disclosure, release validation, immutable artifacts, active pointer, Next.js static export, object-storage paths, and CDN cache headers with no public runtime API.
    - Ensure one command/workflow can build an inactive synthetic release and web bundle without activating it.
    - _Requirements: 8.1-8.8, 14.9-14.11, 15.3, 15.19_
  - [ ] 8.3 Implement the automated release-acceptance runner
    - Orchestrate arithmetic/provenance/privacy/language/schema/digest/accessibility/security/citation/terms checks, emit machine-readable and human-readable reports, and prevent activation on failure or incomplete validation.
    - Include curator sign-off inputs as authenticated versioned records rather than a non-code manual task.
    - _Requirements: 8.2, 8.3, 12.12, 12.13, 15.14, 15.15_
  - [ ] 8.4 Implement repeatable performance-budget instrumentation and gates
    - Define the representative mobile/desktop profile and automate compressed overview size, cached interaction latency, LCP p75, lazy detail loading, creator page sizing, and regression thresholds.
    - Fail release eligibility when the 250 KiB, 100 ms, or 2.5 s bounds are not demonstrated under the documented profile.
    - _Requirements: 14.1-14.5_
  - [ ] 8.5 Implement CI/CD security and release-readiness workflows
    - Run pinned lockfile/license/vulnerability, secret, public-artifact privacy, neutral-copy, schema, test, accessibility, performance, and static-build checks using workload identity and managed secrets.
    - Package only verified immutable artifacts, require explicit secured activation, retain rollback inputs, and prevent direct publication from failed branches or anonymous clients.
    - _Requirements: 7.1-7.11, 8.2-8.12, 12.12, 14.1-14.11, 15.1-15.22_
  - [ ] 8.6 Final checkpoint — validate complete design and requirements alignment
    - Reread all of `design.md` and `requirements.md`; trace every acceptance criterion to completed code/tests, execute the full pipeline, web, property, contract, integration, end-to-end, accessibility, visual, performance, security/privacy, and release-validation suite, inspect generated public artifacts, and correct every drift or failure before declaring implementation complete.
    - Ensure all tests pass, ask the user if questions arise.
  - [ ]* 8.7 Write full synthetic pipeline-to-browser end-to-end tests
    - Exercise registration through extraction, interrupted enrichment, aggregate/release build, activation, default overview, filters, comparison toggle, map/table parity, Unknown Country, detail, pagination, methodology, degraded delivery, and rollback.
    - _Requirements: 1.1-1.9, 2.1-2.16, 3.1-3.12, 4.1-4.18, 5.1-5.13, 6.1-6.11, 8.1-8.12, 9.1-9.12, 10.1-10.12, 11.1-11.10, 12.1-12.13, 14.6-14.11_
  - [ ]* 8.8 Write automated accessibility and visual-regression suites
    - Validate applicable WCAG 2.2 AA rules, semantic states, keyboard paths, live regions, 200% zoom, reduced motion, desktop/mobile reflow, non-color legends, dark views, no-data/selected states, long names, and high-count formatting.
    - _Requirements: 9.4, 9.5, 9.9, 10.12, 13.1-13.11_
  - [ ]* 8.9 Write performance acceptance tests
    - Measure compressed overview payload, cached filter/country updates, mobile LCP p75, lazy shard requests, creator pagination payloads, and immutable/refreshable cache behavior under the representative profile.
    - _Requirements: 14.1-14.5, 14.10, 14.11_
  - [ ]* 8.10 Write adversarial security, privacy, and release-gate tests
    - Inject secrets, restricted identifiers, malicious archives/paths/formulas, unauthorized admin/access attempts, audit failures, disallowed egress, dependency-scan failure, CSP violations, suppressed creators, corrupt artifacts, and mixed releases; prove fail-closed behavior and previous-release preservation.
    - _Requirements: 7.1-7.8, 8.2-8.12, 15.1-15.22_

## Notes

- Tasks marked with `*` are optional test tasks and can be skipped for a faster MVP; mandatory checkpoints still run the relevant available validation and repair drift.
- The 40 non-test implementation leaves are grouped in sets of five. Tasks 1.6, 2.6, 3.6, 4.6, 5.6, 6.6, 7.6, and 8.6 are non-optional barriers after implementation leaves 5, 10, 15, 20, 25, 30, 35, and 40 respectively.
- Each property task maps one numbered design invariant to accepted requirement clauses without changing the design's numbering.
- All 15 requirements are covered by implementation and validation tasks; acceptance-criterion ranges are cited at leaf level for traceability.
- No task deploys to production, performs manual user acceptance, gathers live performance metrics, or downloads/redistributes media. Release and deployment work creates code, configuration, automated checks, and inactive artifacts only.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.4"] },
    { "id": 2, "tasks": ["1.3", "1.5"] },
    { "id": 3, "tasks": ["1.7"] },
    { "id": 4, "tasks": ["1.6"] },
    { "id": 5, "tasks": ["2.1", "2.2"] },
    { "id": 6, "tasks": ["2.3"] },
    { "id": 7, "tasks": ["2.4"] },
    { "id": 8, "tasks": ["2.5"] },
    { "id": 9, "tasks": ["2.7", "2.8", "2.9"] },
    { "id": 10, "tasks": ["2.6"] },
    { "id": 11, "tasks": ["3.1"] },
    { "id": 12, "tasks": ["3.2", "3.3"] },
    { "id": 13, "tasks": ["3.4"] },
    { "id": 14, "tasks": ["3.5"] },
    { "id": 15, "tasks": ["3.7", "3.8", "3.9"] },
    { "id": 16, "tasks": ["3.6"] },
    { "id": 17, "tasks": ["4.1", "4.4"] },
    { "id": 18, "tasks": ["4.2", "4.3"] },
    { "id": 19, "tasks": ["4.5"] },
    { "id": 20, "tasks": ["4.7", "4.8", "4.9", "4.10", "4.11", "4.12", "4.13", "4.14"] },
    { "id": 21, "tasks": ["4.6"] },
    { "id": 22, "tasks": ["5.1"] },
    { "id": 23, "tasks": ["5.2", "5.3"] },
    { "id": 24, "tasks": ["5.4"] },
    { "id": 25, "tasks": ["5.5"] },
    { "id": 26, "tasks": ["5.7", "5.8", "5.9"] },
    { "id": 27, "tasks": ["5.6"] },
    { "id": 28, "tasks": ["6.1", "6.2"] },
    { "id": 29, "tasks": ["6.3"] },
    { "id": 30, "tasks": ["6.4"] },
    { "id": 31, "tasks": ["6.5"] },
    { "id": 32, "tasks": ["6.7", "6.8"] },
    { "id": 33, "tasks": ["6.6"] },
    { "id": 34, "tasks": ["7.1"] },
    { "id": 35, "tasks": ["7.2"] },
    { "id": 36, "tasks": ["7.3"] },
    { "id": 37, "tasks": ["7.4"] },
    { "id": 38, "tasks": ["7.5"] },
    { "id": 39, "tasks": ["7.7", "7.8"] },
    { "id": 40, "tasks": ["7.6"] },
    { "id": 41, "tasks": ["8.1"] },
    { "id": 42, "tasks": ["8.2"] },
    { "id": 43, "tasks": ["8.3"] },
    { "id": 44, "tasks": ["8.4"] },
    { "id": 45, "tasks": ["8.5"] },
    { "id": 46, "tasks": ["8.7", "8.8", "8.9", "8.10"] },
    { "id": 47, "tasks": ["8.6"] }
  ]
}
```
