# Requirements Document

## Introduction

The YouTube Creator Training Data Map is a public, interactive map that summarizes YouTube video identifiers observed in approved, versioned dataset source materials. The system resolves available channel metadata, groups creators by country declared in YouTube channel metadata, and publishes reproducible aggregates with explicit provenance, coverage, counting units, filters, and limitations. The system reports observations only; the system does not determine training, copyright status, legality, consent, residence, or nationality.

## Glossary

- **System**: The complete ingestion, enrichment, aggregation, publication, and public exploration feature.
- **Dataset_Registry**: The restricted catalog of dataset versions and their review, provenance, source-kind, corpus-class, and occurrence-semantics metadata.
- **Dataset_Contract**: A versioned registry record that identifies one dataset snapshot, its immutable digest, adapter version, access status, source citation, source kind, corpus class, occurrence unit, acquisition path, and terms review.
- **Dataset_Contract_Key**: The unique pair of dataset identifier and dataset version that addresses one Dataset_Contract.
- **Approved_Dataset_Contract**: A Dataset_Contract whose access status is approved and whose required metadata is complete.
- **Source_Snapshot**: Immutable, digest-addressed source metadata or index material for one dataset version.
- **Source_Adapter**: A source-specific restricted component that validates a Source_Snapshot and extracts normalized occurrences and rejects.
- **Supported_ID_Grammar**: The versioned grammar consisting of a bare canonical YouTube video identifier and the supported YouTube URL forms from which exactly one identifier can be extracted.
- **Canonical_Video_ID**: The single normalized representation of a syntactically valid YouTube video identifier.
- **Canonical_ID_Printer**: The deterministic component that serializes a Canonical_Video_ID as the bare-identifier form of the Supported_ID_Grammar.
- **Normalized_Occurrence**: An accepted source occurrence containing dataset ID, snapshot digest, source locator, Canonical_Video_ID, occurrence unit, extraction time, adapter version, and optional clip bounds.
- **Source_Locator**: An opaque identifier that locates a source record within a Source_Snapshot and remains restricted by default.
- **Extraction_Report**: A versioned report of examined records, accepted source records, emitted Normalized_Occurrence records, rejected records, expansion counts, schema version, and rejection reasons for one adapter run.
- **Identity_Resolver**: The deterministic component that parses and normalizes video identifiers and plans distinct enrichment work.
- **Enrichment_Service**: The restricted batch component that obtains video-to-channel and channel metadata observations from the approved YouTube metadata API.
- **Enrichment_Policy**: A versioned, approved policy defining observation freshness, required metadata fields, retry classification, cutoff selection, and deterministic observation tie-breaking.
- **Enrichment_Cutoff**: The release-pinned instant after which Resolution_Observation records are ineligible for a release.
- **Resolution_Observation**: An append-only, time-stamped video or channel enrichment result with a response digest when a response is available.
- **Selected_Observation**: The single Resolution_Observation selected for an identity by the release-pinned Enrichment_Policy and Enrichment_Cutoff.
- **Unavailable_Unclassified**: A resolution status for an identifier omitted or unavailable without authoritative evidence for a more specific status.
- **Declared_Country**: A supported ISO 3166 alpha-2 country code obtained only from the YouTube channel country metadata field.
- **Unknown_Country**: The bucket for a resolved channel with absent or unsupported Declared_Country metadata.
- **Work_Planner**: The restricted component that creates, leases, retries, checkpoints, and resumes distinct video and channel enrichment work.
- **Work_Item**: One versioned unit of video or channel enrichment identified by entity kind, entity identifier, and Enrichment_Policy version.
- **Lease**: A time-bounded exclusive claim on a Work_Item by one authenticated worker.
- **Checkpoint**: An atomic durable transaction containing batch outcomes, Work_Item state transitions, and quota usage.
- **Retry_Policy**: The versioned approved classification, attempt bound, delay bound, exponential-backoff, and jitter rules for retryable enrichment failures.
- **Operator_Halt**: A durable job state that prevents claims and retries until an authenticated operator records an approved recovery action.
- **Quota_Reserve**: The configured amount of daily API quota that batch processing preserves rather than consumes.
- **Active_Filter**: The selected set of dataset IDs and corpus classes used to derive a view or aggregate.
- **Corpus_Class**: A registry label distinguishing candidate corpora from comparison corpora without assigning a legal or moral conclusion.
- **Aggregate_Builder**: The deterministic batch component that computes exact country, creator, dataset, occurrence, and coverage aggregates from pinned inputs.
- **Represented_Video_Count**: The number of distinct Canonical_Video_ID values within an Active_Filter.
- **Source_Occurrence_Count**: The number of retained source rows, clips, timestamps, segments, or video records within an Active_Filter.
- **Creator_Count**: The number of distinct resolved channel identifiers assigned to a selected country bucket.
- **Video_Resolution_Partition**: The mutually exclusive resolved, Unavailable_Unclassified, retryable-or-pending, invalid, and terminal-failure states assigned to distinct input videos.
- **Coverage_Summary**: Counts of input occurrences, distinct input videos, the Video_Resolution_Partition, resolved channels, known-country channels, and unknown-country channels.
- **Disclosure_Policy**: A versioned, approved publication policy defining allowed creator fields, thresholds, risk rules, correction records, opt-outs, and suppressions.
- **Disclosure_Validator**: The publication-boundary component that enforces the Disclosure_Policy before public artifacts are generated.
- **Public_Channel_Key**: A disclosure-approved public key for a channel that is distinct from raw source identifiers and permitted only for a creator that passes the Disclosure_Policy.
- **Release_Candidate**: A complete, inactive set of aggregates, detail shards, methodology, disclosures, and manifest metadata proposed for publication.
- **Release_Manifest**: The versioned public index containing release ID, generation time, Enrichment_Cutoff, included snapshots, default filter, Artifact_Digests, methodology version, and Disclosure_Policy version.
- **Artifact_Digest**: A cryptographic digest recorded in the Release_Manifest for verification of one immutable Public_Artifact.
- **Active_Release_Pointer**: The separately refreshable reference that identifies one complete Verified_Release as active.
- **Release_Manager**: The component that validates, stages, activates, rolls back, and preserves immutable releases.
- **Verified_Release**: A Release_Candidate that has passed every required release gate and Artifact_Digest verification.
- **Public_Artifact**: A disclosure-reviewed JSON, GeoJSON, HTML, JavaScript, style, or metadata file delivered to public clients.
- **CDN**: The content-delivery network that caches immutable Public_Artifact files and the separately refreshable Active_Release_Pointer.
- **Content Security Policy**: The approved browser policy that restricts the sources and execution of public application resources.
- **Subresource Integrity**: Browser-enforced digest validation for an applicable externally hosted resource.
- **Map_Application**: The static-first public web application that presents release summaries, map and table exploration, country details, creator pages, and methodology.
- **View_State_Codec**: The deterministic component that parses and serializes URL-addressable release, filter, country, page, and sort state.
- **Canonical_URL**: The unique URL serialization produced by the View_State_Codec for one valid view state using deterministic ordering and omission rules.
- **Fallback_View**: The valid, canonical view selected by the documented versioned fallback policy after URL-state validation fails.
- **Country_Summary**: Aggregate counts for one Declared_Country or Unknown_Country bucket under one release and Active_Filter.
- **Country_Detail**: The disclosure-reviewed totals, coverage, dataset breakdown, and paginated creator data for one Country_Summary.
- **Creator_Page**: One cursor-addressed, deterministically sorted page of disclosure-approved creator rows for a Country_Detail.
- **Empty_State**: A non-error presentation that explicitly reports that a valid release, Active_Filter, country selection, or creator page has no matching publishable data.
- **Comparison_Corpus**: A corpus labeled from documented provenance or permissions and presented separately from candidate corpora.
- **Publication_Boundary**: The boundary between restricted source/provenance processing and publicly delivered artifacts.
- **Representative_Test_Profile**: The documented hardware, browser, network, cache, dataset, and measurement conditions used for repeatable performance validation.
- **Restricted_Data**: Source_Snapshot files, raw API responses, Source_Locator values, provenance joins, credentials, and job internals that cannot cross the Publication_Boundary.
- **Administrative_Operation**: A curator, release, policy, security, or restricted-data action unavailable to anonymous public clients.
- **Approved_Security_Policy**: The versioned policy defining authorized roles, encryption configuration, untrusted-input limits, approved egress endpoints, dependency-vulnerability thresholds, authentication, rate limits, retention, and audit requirements.
- **Audit_Log**: The durable restricted record of authenticated actor, action, resource class, timestamp, and outcome for a security-relevant operation.
- **WCAG_2_2_AA**: The Web Content Accessibility Guidelines 2.2 Level AA conformance target.
- **Largest_Contentful_Paint**: The web performance metric used by the documented Representative_Test_Profile to measure loading performance.

## Requirements

### Requirement 1: Govern Dataset Sources

**User Story:** As a curator, I want each dataset version to pass an explicit source review, so that published observations have documented provenance and permitted acquisition.

#### Acceptance Criteria

1. THE Dataset_Registry SHALL derive one Dataset_Contract_Key from the dataset identifier and dataset version of each Dataset_Contract.
2. WHEN a Dataset_Contract_Key is absent from the Dataset_Registry, THE Dataset_Registry SHALL store exactly one Dataset_Contract containing the source citation, acquisition path, occurrence semantics, source kind, Corpus_Class, immutable snapshot digest, adapter version, access status, and terms-review identifier.
3. WHEN a submitted Dataset_Contract exactly matches the stored Dataset_Contract for the same Dataset_Contract_Key, THE Dataset_Registry SHALL return the stored Dataset_Contract without creating a duplicate or changing registry state.
4. IF a submitted Dataset_Contract has the same Dataset_Contract_Key and differs from the stored Dataset_Contract in any contract field, THEN THE Dataset_Registry SHALL reject the submission and preserve the stored Dataset_Contract unchanged.
5. IF a Dataset_Contract lacks approved access status or any required contract field, THEN THE Dataset_Registry SHALL exclude the dataset version from extraction and publication and record the validation reasons.
6. WHEN a Source_Snapshot is supplied for extraction, THE Source_Adapter SHALL verify that the Source_Snapshot digest equals the digest in the Approved_Dataset_Contract before emitting a Normalized_Occurrence.
7. IF the Source_Snapshot digest differs from the Approved_Dataset_Contract digest, THEN THE Source_Adapter SHALL emit no Normalized_Occurrence and SHALL record a failed validation result for the Dataset_Contract_Key.
8. WHEN an approved Dataset_Contract is revised, THE Dataset_Registry SHALL require a new dataset version and Dataset_Contract_Key while preserving the prior Dataset_Contract unchanged.
9. WHEN the methodology identifies an included dataset, THE Map_Application SHALL display the dataset identifier, dataset version, source citation, source kind, occurrence semantics, and immutable snapshot reference recorded by the included Dataset_Contract.

### Requirement 2: Normalize and Account for Source Records

**User Story:** As a data auditor, I want source records normalized with complete provenance and explicit rejection accounting, so that extraction results are reproducible.

#### Acceptance Criteria

1. WHEN untrusted input conforms to the Supported_ID_Grammar, THE Identity_Resolver SHALL return exactly one Canonical_Video_ID.
2. WHEN the same untrusted input and Supported_ID_Grammar version are evaluated more than once, THE Identity_Resolver SHALL return the same normalization result and rejection reason.
3. WHEN a Canonical_Video_ID is normalized again, THE Identity_Resolver SHALL return the same Canonical_Video_ID.
4. THE Canonical_ID_Printer SHALL serialize each Canonical_Video_ID as the bare-identifier form of the Supported_ID_Grammar.
5. WHEN a Canonical_Video_ID is printed, parsed, printed, and parsed, THE Identity_Resolver SHALL produce the original Canonical_Video_ID after both parse operations and THE Canonical_ID_Printer SHALL produce the same text after both print operations.
6. IF untrusted input fails any rule of the Supported_ID_Grammar regardless of detection path, THEN THE Identity_Resolver SHALL return a non-empty versioned rejection reason without creating a Normalized_Occurrence.
7. WHEN the Source_Adapter accepts a source occurrence and every required field can be recorded, THE Source_Adapter SHALL record the dataset identifier, snapshot digest, Source_Locator, Canonical_Video_ID, occurrence unit, extraction time, adapter version, and applicable clip bounds.
8. IF a source occurrence has passed every acceptance validation but the dataset identifier cannot be persisted, THEN THE Source_Adapter SHALL classify the source record as accepted for extraction accounting, preserve the other available fields, exclude the incomplete record from Normalized_Occurrence publication, and require provenance completion before publication.
9. WHEN accepted source data contains clip bounds, THE Source_Adapter SHALL accept the bounds only when zero is less than or equal to the start and the start is less than the end.
10. IF source data contains invalid clip bounds, THEN THE Source_Adapter SHALL quarantine the source record with a non-empty rejection reason without emitting a Normalized_Occurrence for the invalid bounds.
11. WHEN a source record contains repeated valid clips, timestamps, segments, or rows, THE Source_Adapter SHALL retain each valid Normalized_Occurrence without deduplicating source evidence.
12. WHEN extraction completes without schema drift, THE Source_Adapter SHALL classify each examined source record exactly once as accepted or rejected.
13. WHEN one accepted source record emits multiple Normalized_Occurrence records, THE Extraction_Report SHALL record the exact emitted count and expansion count for that source record.
14. WHEN extraction completes without schema drift, THE Extraction_Report SHALL make accepted source-record count plus rejected source-record count equal examined source-record count and SHALL make emitted Normalized_Occurrence count equal the sum of per-record emitted counts.
15. IF a source record is malformed or unsupported, THEN THE Source_Adapter SHALL quarantine the record with dataset identifier, snapshot digest, Source_Locator, adapter version, and a non-empty rejection reason.
16. IF adapter validation detects source schema drift, THEN THE Source_Adapter SHALL fail the extraction run closed, publish no Normalized_Occurrence from the run, preserve prior completed extraction outputs unchanged, and require a versioned adapter update and new Extraction_Report.

### Requirement 3: Enrich Distinct Identities

**User Story:** As an operator, I want enrichment to resolve each distinct identity once per policy version, so that quota is conserved and dataset overlap remains traceable.

#### Acceptance Criteria

1. WHEN video enrichment work is planned for an Enrichment_Policy version, THE Identity_Resolver SHALL create at most one Work_Item for each distinct Canonical_Video_ID eligible under that Enrichment_Policy.
2. WHEN channel enrichment work is planned for an Enrichment_Policy version, THE Enrichment_Service SHALL create at most one Work_Item for each distinct resolved channel identifier eligible under that Enrichment_Policy.
3. THE Enrichment_Service SHALL preserve dataset membership as a many-to-many relationship between dataset versions and Canonical_Video_ID values without copying Resolution_Observation records per dataset.
4. WHEN a cached Resolution_Observation satisfies the Enrichment_Policy and Enrichment_Cutoff, THE Enrichment_Service SHALL reuse the cached Resolution_Observation without issuing a duplicate metadata request.
5. WHEN the video metadata API resolves a requested Canonical_Video_ID, THE Enrichment_Service SHALL append a Resolution_Observation containing the resolved channel identifier, observation time, status, and response digest.
6. IF the video metadata API omits a requested Canonical_Video_ID without authoritative finer status, THEN THE Enrichment_Service SHALL append an Unavailable_Unclassified Resolution_Observation with the observation time.
7. WHEN the channel metadata API resolves a requested channel, THE Enrichment_Service SHALL append a Resolution_Observation containing only the approved display fields, Declared_Country when present, observation time, status, and response digest.
8. IF a resolved channel has an absent or unsupported Declared_Country, THEN THE Enrichment_Service SHALL append a resolved Resolution_Observation assigned to Unknown_Country without inferring a country from another field.
9. WHEN a new enrichment result is committed, THE Enrichment_Service SHALL append a new Resolution_Observation without updating or deleting an existing Resolution_Observation.
10. WHEN a release selects observations, THE Enrichment_Service SHALL exclude every Resolution_Observation later than the release-pinned Enrichment_Cutoff.
11. WHEN multiple eligible Resolution_Observation records exist for one identity, THE Enrichment_Service SHALL select exactly one Selected_Observation using the deterministic ordering and tie-breaking rules of the release-pinned Enrichment_Policy.
12. WHEN release inputs, Enrichment_Policy version, and Enrichment_Cutoff are unchanged, THE Enrichment_Service SHALL produce the same Selected_Observation for every identity.

### Requirement 4: Resume Enrichment Within Quota and Failure Policies

**User Story:** As an operator, I want enrichment jobs to resume safely after interruption or service failures, so that partial work is preserved without duplicate committed results.

#### Acceptance Criteria

1. THE Work_Planner SHALL identify each Work_Item uniquely by entity kind, entity identifier, and Enrichment_Policy version.
2. WHEN a worker claims eligible work, THE Work_Planner SHALL create Leases for no more than 50 distinct Work_Item values in one metadata batch.
3. WHILE a Lease remains unexpired, THE Work_Planner SHALL associate the leased Work_Item with exactly one authenticated worker and exclude the Work_Item from other claims.
4. WHEN a Lease expires without a committed result, THE Work_Planner SHALL make the Work_Item eligible for a later claim based on lease expiry even when the stored Work_Item state remains leased, without changing a previously committed Resolution_Observation.
5. WHEN an API batch succeeds, THE Work_Planner SHALL commit batch outcomes, Work_Item state transitions, and quota usage in one Checkpoint.
6. IF any part of a Checkpoint cannot commit, THEN THE Work_Planner SHALL commit none of the Checkpoint and SHALL leave the affected Work_Item values recoverable from the preceding Checkpoint.
7. WHEN an already committed Checkpoint is replayed, THE Work_Planner SHALL preserve the committed Resolution_Observation set, Work_Item states, and quota usage without duplicate effects.
8. IF a failure is classified as retryable and the affected Work_Item remains below the Retry_Policy attempt bound, THEN THE Work_Planner SHALL retain the Work_Item in a retryable state and schedule the next attempt with bounded exponential backoff and jitter.
9. IF a Work_Item reaches the Retry_Policy attempt bound, THEN THE Work_Planner SHALL apply the terminal or operator-review state defined by the Retry_Policy and record the final error class.
10. IF a failure is classified as non-retryable by the Retry_Policy, THEN THE Work_Planner SHALL place the affected Work_Item directly in terminal-failure state and record the error class without scheduling another attempt.
11. IF an invalid credential or policy block occurs, THEN THE Work_Planner SHALL place the affected job in Operator_Halt and produce an operator alert without scheduling another metadata request.
12. WHILE a job is in Operator_Halt, THE Work_Planner SHALL issue no new claims or retries for the job.
13. WHEN an authenticated operator records an approved recovery action for an Operator_Halt, THE Work_Planner SHALL resume only the Work_Item states authorized by that recovery action.
14. WHILE remaining daily quota is less than or equal to the Quota_Reserve, THE Work_Planner SHALL stop claiming work with a positive projected quota cost and preserve the latest committed Checkpoint.
15. WHEN the positive projected quota cost of a new batch would consume the Quota_Reserve, THE Work_Planner SHALL leave the batch unclaimed.
16. WHEN an eligible batch has zero projected quota cost, THE Work_Planner SHALL permit the batch claim regardless of the Quota_Reserve state.
17. WHEN an interrupted job resumes with the same API observations, Enrichment_Policy, and Enrichment_Cutoff, THE Work_Planner SHALL produce the same committed Resolution_Observation set as uninterrupted execution.
18. WHEN the Enrichment_Service requests metadata, THE Enrichment_Service SHALL request only the fields required for approved display, attribution, country, status, and provenance data.

### Requirement 5: Compute Exact Filtered Aggregates

**User Story:** As a visitor, I want counts with explicit units and exact filter semantics, so that duplicate source records and overlapping datasets do not inflate headline totals.

#### Acceptance Criteria

1. WHEN the Aggregate_Builder computes a Represented_Video_Count, THE Aggregate_Builder SHALL count the cardinality of the distinct Canonical_Video_ID set selected by the Active_Filter.
2. WHEN duplicate Normalized_Occurrence records for the same dataset version and Canonical_Video_ID are added, THE Aggregate_Builder SHALL increase Source_Occurrence_Count by the number of added Normalized_Occurrence records without changing Represented_Video_Count.
3. WHEN datasets in an Active_Filter contain the same Canonical_Video_ID, THE Aggregate_Builder SHALL count the Canonical_Video_ID once in the combined Represented_Video_Count and once in each applicable dataset breakdown.
4. WHEN the Aggregate_Builder computes a Creator_Count, THE Aggregate_Builder SHALL count the cardinality of the distinct resolved channel-identifier set in the selected country bucket and Active_Filter.
5. WHEN a resolved video is attributed through a Selected_Observation, THE Aggregate_Builder SHALL assign the Canonical_Video_ID to exactly one resolved channel and exactly one Declared_Country or Unknown_Country bucket.
6. IF a video lacks a resolved channel Selected_Observation, THEN THE Aggregate_Builder SHALL assign the Canonical_Video_ID to exactly one non-resolved Video_Resolution_Partition state without including the Canonical_Video_ID in the resolved state, Creator_Count, or country-attributed Represented_Video_Count.
7. IF a resolved channel Selected_Observation lacks a supported Declared_Country, THEN THE Aggregate_Builder SHALL assign the channel and associated resolved videos to Unknown_Country.
8. WHEN an Active_Filter is applied, THE Aggregate_Builder SHALL include a Normalized_Occurrence only when the occurrence dataset identifier and Corpus_Class are both included by the Active_Filter.
9. WHEN an Active_Filter is applied, THE Aggregate_Builder SHALL exclude every Normalized_Occurrence whose dataset identifier or Corpus_Class is excluded by the Active_Filter.
10. WHEN one Active_Filter is a subset of another within the same Verified_Release, THE Aggregate_Builder SHALL produce a Canonical_Video_ID set for the subset that is a subset of the Canonical_Video_ID set for the superset.
11. WHEN one Active_Filter is a subset of another within the same Verified_Release, THE Aggregate_Builder SHALL produce Represented_Video_Count and resolved Creator_Count values for the subset that do not exceed the corresponding superset values.
12. THE Aggregate_Builder SHALL compute public headline counts and dataset breakdowns with exact distinct-set operations rather than additive approximations.
13. WHEN the same pinned inputs and Active_Filter are built more than once, THE Aggregate_Builder SHALL produce byte-equivalent aggregate values and deterministically ordered records.

### Requirement 6: Report Resolution Coverage

**User Story:** As a visitor, I want unresolved and unknown-country records reported beside headline totals, so that I can assess the coverage of each view.

#### Acceptance Criteria

1. WHEN the Aggregate_Builder computes a Coverage_Summary, THE Aggregate_Builder SHALL report Source_Occurrence_Count and the distinct input Canonical_Video_ID count for the Active_Filter.
2. WHEN the Aggregate_Builder computes a Coverage_Summary, THE Aggregate_Builder SHALL assign each distinct input Canonical_Video_ID to exactly one Video_Resolution_Partition state.
3. WHEN the Aggregate_Builder computes a Coverage_Summary, THE Aggregate_Builder SHALL make the resolved, Unavailable_Unclassified, retryable-or-pending, invalid, and terminal-failure Canonical_Video_ID sets mutually disjoint.
4. WHEN the Aggregate_Builder computes a Coverage_Summary, THE Aggregate_Builder SHALL make the sum of Video_Resolution_Partition state counts equal the distinct input Canonical_Video_ID count.
5. WHEN the Aggregate_Builder computes channel coverage, THE Aggregate_Builder SHALL make known-country-channel count plus unknown-country-channel count equal resolved-channel count.
6. WHEN the Aggregate_Builder computes channel coverage, THE Aggregate_Builder SHALL count each distinct resolved channel in exactly one of the known-country-channel and unknown-country-channel sets.
7. WHEN the Map_Application displays headline totals, THE Map_Application SHALL display the Active_Filter, release date, Enrichment_Cutoff, Source_Occurrence_Count, distinct input-video count, and Video_Resolution_Partition counts.
8. WHEN Unknown_Country contains resolved channels, THE Map_Application SHALL display Unknown_Country as a summary card and table row outside the geographic choropleth.
9. IF the metadata API supplies no authoritative status finer than Unavailable_Unclassified, THEN THE Map_Application SHALL label the corresponding coverage as unavailable or unresolved-unclassified without labeling the identifiers deleted or private.
10. WHEN the metadata API supplies an authoritative finer status supported by the Enrichment_Policy, THE Map_Application SHALL display the authoritative status label while preserving the Canonical_Video_ID assignment to exactly one Video_Resolution_Partition state.
11. WHEN an Active_Filter produces no input occurrences, THE Map_Application SHALL display headline totals and an Empty_State with zero input counts and SHALL distinguish the Empty_State from loading, delivery-failure, and digest-failure states.

### Requirement 7: Enforce Disclosure and Privacy Policy

**User Story:** As a privacy reviewer, I want creator detail governed by a versioned disclosure policy, so that public exploration minimizes discoverability and re-identification risks.

#### Acceptance Criteria

1. WHILE the Disclosure_Policy is absent, unapproved, invalid, or missing a required threshold or risk rule, THE Disclosure_Validator SHALL reject every Release_Candidate.
2. WHEN a creator satisfies every creator-level condition of the release-pinned Disclosure_Policy, THE Disclosure_Validator SHALL permit only the subset of Public_Channel_Key, approved display name, country, Represented_Video_Count, dataset breakdown, and metadata observation date that also passes every applicable field-level validation rule.
3. IF a creator fails any Disclosure_Policy condition, THEN THE Disclosure_Validator SHALL exclude Public_Channel_Key and every creator-identifying field from Public_Artifact files, generated search indexes, sitemaps, public logs, client telemetry, error messages, and downloadable payloads.
4. IF a correction, opt-out, or suppression record matches a creator, THEN THE Disclosure_Validator SHALL apply the release-pinned exclusion rule before generating a Public_Artifact.
5. IF a proposed Public_Artifact contains a raw video identifier, Source_Locator, contact field, raw API response, Restricted_Data value, or restricted provenance join, THEN THE Disclosure_Validator SHALL reject the Release_Candidate.
6. WHEN the Disclosure_Validator evaluates a Public_Artifact, THE Disclosure_Validator SHALL recursively inspect keys, values, embedded metadata, and generated indexes for prohibited identifiers and fields.
7. IF disclosure validation cannot determine whether a field or creator is permitted, THEN THE Disclosure_Validator SHALL treat the field or creator as prohibited and reject the affected Release_Candidate.
8. WHEN Disclosure_Policy changes invalidate an active creator shard, THE Release_Manager SHALL remove the shard from active delivery using the policy-defined invalidation procedure without exposing the suppression reason.
9. THE Map_Application SHALL publish the documented correction, opt-out, and suppression review path approved by the Disclosure_Policy.
10. THE Map_Application SHALL apply search-engine exclusion metadata to creator-detail routes and SHALL omit creator-detail shards from generated search indexes and sitemaps.
11. WHERE operational telemetry is enabled, THE Map_Application SHALL emit only policy-approved coarse events without channel identifiers, video identifiers, free text, precise location, or stable visitor identifiers.

### Requirement 8: Validate and Publish Immutable Releases

**User Story:** As a curator, I want releases validated and activated atomically, so that visitors receive one internally consistent and reproducible data version.

#### Acceptance Criteria

1. WHEN a Release_Candidate is assembled, THE Release_Manager SHALL record immutable snapshot references, Enrichment_Cutoff, default Active_Filter, methodology version, Disclosure_Policy version, generation time, and one Artifact_Digest for every Public_Artifact in the Release_Manifest.
2. WHEN activation is requested, THE Release_Manager SHALL verify arithmetic reconciliation, provenance completeness, disclosure compliance, neutral-language review, artifact schemas, Artifact_Digests, accessibility smoke checks, dataset citations, terms-review approval, and required security gates.
3. IF any required release gate fails or cannot complete, THEN THE Release_Manager SHALL reject activation, keep the previous Verified_Release active, and produce an internal validation report identifying each failed or incomplete gate.
4. WHEN every required release gate passes, THE Release_Manager SHALL stage the complete versioned Public_Artifact set before changing the Active_Release_Pointer.
5. WHEN staged Public_Artifact verification occurs, THE Release_Manager SHALL recompute every Artifact_Digest and require equality with the corresponding Release_Manifest value.
6. IF any staged Public_Artifact is absent or has an Artifact_Digest mismatch, THEN THE Release_Manager SHALL leave the Active_Release_Pointer unchanged and SHALL remove the Release_Candidate from activation eligibility.
7. WHEN the Active_Release_Pointer changes, THE Release_Manager SHALL expose either the complete previous Verified_Release or the complete new Verified_Release at every observable instant.
8. WHEN a Verified_Release is active, THE Release_Manager SHALL serve only Public_Artifact files whose Artifact_Digests match the active Release_Manifest.
9. WHEN later Source_Snapshot or Resolution_Observation records arrive, THE Release_Manager SHALL preserve the Release_Manifest, Artifact_Digests, and counts of every previously published Verified_Release unchanged.
10. WHEN rollback is requested and identifies a target prior Verified_Release, THE Release_Manager SHALL verify the target prior Release_Manifest and every referenced Artifact_Digest before changing the Active_Release_Pointer.
11. IF rollback verification fails, THEN THE Release_Manager SHALL preserve the current Verified_Release and SHALL produce an internal rollback validation report.
12. WHEN rollback verification passes, THE Release_Manager SHALL restore the complete prior Verified_Release through one atomic Active_Release_Pointer change.

### Requirement 9: Present the Map Overview and Filters

**User Story:** As a visitor, I want a map and equivalent table with explicit filters and count definitions, so that I can explore country-level observations without misreading the data.

#### Acceptance Criteria

1. WHEN the Map_Application loads a Verified_Release, THE Map_Application SHALL display Creator_Count, Represented_Video_Count, represented country count, resolution coverage, release date, Enrichment_Cutoff, and the Active_Filter.
2. WHEN the Map_Application renders a geographic summary, THE Map_Application SHALL derive the choropleth and sortable country table from the same Country_Summary records.
3. WHEN a Country_Summary appears in both the choropleth and country table, THE Map_Application SHALL present identical country identity, selected metric value, release, and Active_Filter in both representations.
4. WHEN the Map_Application encodes a metric by color, THE Map_Application SHALL display exact legend bin ranges and a distinct no-data style derived from the active metric distribution.
5. WHEN the Map_Application displays a color-encoded value, THE Map_Application SHALL provide the same exact value and state through persistent text, pattern information, keyboard focus, or the country table.
6. WHEN a visitor changes selected datasets or Corpus_Class values, THE Map_Application SHALL update the choropleth, country table, headline summaries, coverage, Active_Filter label, and Canonical_URL to the same resulting Active_Filter.
7. WHEN an Active_Filter has no matching Country_Summary records, THE Map_Application SHALL display an Empty_State in both map and table views without representing the Empty_State as an artifact error or a no-data country.
8. WHERE comparison corpora are selected, THE Map_Application SHALL identify each Comparison_Corpus with provenance-based labels and filter treatment distinct from candidate corpora.
9. WHEN the map provides information through hover, THE Map_Application SHALL provide the identical information through keyboard focus and the country table.
10. WHEN a visitor switches between map and table views, THE Map_Application SHALL preserve the Verified_Release, Active_Filter, selected country, selected metric, and values.
11. WHEN a visitor clears a country selection, THE Map_Application SHALL return to the Active_Filter overview without changing the Verified_Release or Active_Filter.
12. IF a filter-state update cannot be completed, THEN THE Map_Application SHALL preserve the preceding valid Verified_Release and Active_Filter and SHALL present a recoverable error without displaying partial filter results.

### Requirement 10: Present Consistent Country and Creator Details

**User Story:** As a visitor, I want a country drill-down with constrained creator details, so that I can inspect aggregate composition without receiving raw source records.

#### Acceptance Criteria

1. WHEN a visitor selects a country by click, Enter, Space, or country-table action, THE Map_Application SHALL open the Country_Detail for the same Verified_Release and Active_Filter.
2. WHEN the Map_Application displays Country_Detail, THE Map_Application SHALL make Creator_Count and Represented_Video_Count equal the corresponding Country_Summary values for the same country, Verified_Release, and Active_Filter.
3. WHEN the Map_Application displays Country_Detail, THE Map_Application SHALL show Creator_Count, Represented_Video_Count, Source_Occurrence_Count, Coverage_Summary, dataset breakdown, release date, and Enrichment_Cutoff.
4. WHEN the Map_Application displays creator rows, THE Map_Application SHALL show only fields permitted by the release-pinned Disclosure_Policy.
5. WHEN creator results exceed the configured page size, THE Map_Application SHALL partition creator rows into Creator_Page values using the configured page size, versioned sort order, and deterministic tie-breaker.
6. WHEN all Creator_Page values for one Country_Detail are traversed without a filter or sort change, THE Map_Application SHALL present each disclosure-approved creator exactly once without omission.
7. WHEN a visitor requests the same page cursor and sort order for unchanged release, Active_Filter, and country state, THE Map_Application SHALL present the same creator rows in the same order.
8. WHEN a visitor changes creator page or sort order, THE Map_Application SHALL preserve the selected Verified_Release, Active_Filter, and country in visible state and Canonical_URL.
9. WHEN a country selection changes, THE Map_Application SHALL synchronize Canonical_URL, visible heading, map selection, country-table selection, Country_Detail, creator-page cursor, and creator sort order to one valid country state.
10. IF a Country_Detail or Creator_Page request fails validation, delivery, or Artifact_Digest verification, THEN THE Map_Application SHALL preserve the selected Verified_Release, Active_Filter, and country and SHALL display a recoverable error without presenting partial rows or zero totals as valid data.
11. WHEN a Country_Detail has passed validation and contains no disclosure-approved creator rows, THE Map_Application SHALL display an Empty_State while preserving aggregate totals and Disclosure_Policy context.
12. WHERE the viewport is below the configured small-screen breakpoint, THE Map_Application SHALL present Country_Detail as a bottom sheet with the same fields, controls, pagination, sort options, and accessible names as the desktop detail panel.

### Requirement 11: Parse and Serialize URL View State

**User Story:** As a visitor, I want exploration state encoded in the URL, so that I can bookmark and share a reproducible view.

#### Acceptance Criteria

1. WHEN the Map_Application changes release, Active_Filter, country, creator page, or creator sort order, THE View_State_Codec SHALL serialize the complete resulting state as one Canonical_URL.
2. WHEN two equivalent view states differ only in set-member ordering or omitted default values, THE View_State_Codec SHALL serialize both states as the same Canonical_URL.
3. WHEN a URL conforms to the documented versioned view-state grammar, THE View_State_Codec SHALL parse the URL into exactly one view state.
4. WHEN a valid view state is serialized and parsed, THE View_State_Codec SHALL produce an equivalent release, Active_Filter, country, creator page, and creator sort order.
5. WHEN a valid URL is parsed, serialized, and parsed again, THE View_State_Codec SHALL produce equivalent view states after both parse operations and SHALL produce the same Canonical_URL on subsequent serialization.
6. WHEN a Canonical_URL is parsed and serialized, THE View_State_Codec SHALL return the identical Canonical_URL.
7. IF a URL contains an unsupported release, dataset, Corpus_Class, country, page cursor, or sort order, THEN THE View_State_Codec SHALL return a recoverable validation result and one Fallback_View selected by the documented versioned fallback policy.
8. WHEN the View_State_Codec applies a Fallback_View, THE View_State_Codec SHALL serialize the Fallback_View as a valid Canonical_URL.
9. WHEN the Map_Application applies a Fallback_View, THE Map_Application SHALL identify each corrected state component and SHALL exclude invalid values from headings, selections, metrics, and data requests.
10. IF URL parsing cannot load the requested Verified_Release, THEN THE Map_Application SHALL use the release selected by the documented fallback policy and SHALL preserve methodology and error-recovery access.

### Requirement 12: Communicate Provenance and Limitations Neutrally

**User Story:** As a visitor, I want precise methodology and limitation statements, so that I can interpret observed identifiers and channel metadata without unsupported legal or personal conclusions.

#### Acceptance Criteria

1. THE Map_Application SHALL describe dataset membership as a Canonical_Video_ID observed in a named, versioned Source_Snapshot.
2. THE Map_Application SHALL describe country as Declared_Country metadata observed at the published Enrichment_Cutoff.
3. THE Map_Application SHALL describe Represented_Video_Count as distinct source-video identifiers within the Active_Filter.
4. THE Map_Application SHALL describe Source_Occurrence_Count as a secondary count of retained source rows, clips, timestamps, segments, or video records.
5. THE Map_Application SHALL use approved observational language that excludes claims of confirmed model training, infringement, illegality, consent status, residence, or nationality.
6. WHEN the Map_Application describes a Comparison_Corpus, THE Map_Application SHALL base the description on documented provenance and permissions without assigning consent status to another corpus.
7. WHEN the Map_Application displays dataset subtotals, THE Map_Application SHALL disclose that cross-dataset overlap can make dataset subtotals non-additive.
8. WHEN the Map_Application displays a public aggregate, THE Map_Application SHALL identify the Verified_Release, Active_Filter, counting unit, coverage context, and applicable methodology version.
9. THE Map_Application SHALL provide methodology and limitations access from the overview, every Country_Detail view, every Empty_State, and every recoverable artifact-error state.
10. THE Map_Application SHALL document the country-boundary dataset version, attribution, license metadata, naming conventions, and treatment of disputed territories.
11. THE Map_Application SHALL state that geographic boundaries are presentation conventions and are not evidence of channel location beyond Declared_Country metadata.
12. IF public copy is not approved by the neutral-language review gate, THEN THE Release_Manager SHALL reject the Release_Candidate without changing the Active_Release_Pointer.
13. WHEN public copy passes the neutral-language review gate, THE Release_Manager SHALL keep the Release_Candidate eligible for the remaining release gates without treating neutral-language approval as approval of another gate.

### Requirement 13: Provide Accessible and Responsive Exploration

**User Story:** As a visitor using assistive technology or a small-screen device, I want equivalent access to every exploration function, so that map presentation does not create an access barrier.

#### Acceptance Criteria

1. THE Map_Application SHALL satisfy WCAG_2_2_AA success criteria applicable to text, controls, focus indicators, status messages, data visualizations, reflow, and input interaction.
2. THE Map_Application SHALL provide semantic headings, programmatically labeled controls, visible keyboard focus, and a logical focus order for overview, filter, country, detail, sort, and pagination functions.
3. WHEN a filter changes, THE Map_Application SHALL announce the resulting Active_Filter, loading status, completion status, and summary update through an accessible live region.
4. WHEN country selection is available through pointer interaction on the map, THE Map_Application SHALL provide equivalent country discovery and selection through keyboard focus and the country table.
5. WHEN a visitor uses Enter or Space on a keyboard-selectable country control, THE Map_Application SHALL produce the same selected country, Country_Detail, and Canonical_URL as pointer selection.
6. WHILE reduced-motion preference is active, THE Map_Application SHALL replace non-essential animated transitions with non-animated state changes without removing state information or controls.
7. WHILE content is displayed at 200 percent zoom, THE Map_Application SHALL preserve access to all controls, labels, metrics, Empty_State content, errors, and Country_Detail content without two-dimensional page scrolling at the supported viewport baseline.
8. THE Map_Application SHALL expose every hover-disclosed datum through persistent text, focus interaction, or the equivalent country table.
9. WHERE a screen reader is used, THE Map_Application SHALL expose Active_Filter, loading state, Empty_State, error state, country selection, summary values, sort state, and creator-pagination state with programmatic names, relationships, and status messages.
10. WHERE the viewport is below the configured small-screen breakpoint, THE Map_Application SHALL provide the same filter, map-alternative, country-selection, detail, sort, pagination, methodology, and recovery functions available at the desktop breakpoint.
11. WHEN accessibility equivalence is validated, THE Map_Application SHALL produce matching selected values and resulting view state for pointer, keyboard, screen-reader, map, and table interaction paths that perform the same action.

### Requirement 14: Meet Delivery, Recovery, and Performance Bounds

**User Story:** As a visitor, I want a cacheable and resilient application, so that exploration remains understandable under normal and degraded delivery conditions.

#### Acceptance Criteria

1. WHEN the default overview is delivered, THE Release_Manager SHALL keep the compressed Release_Manifest plus country-summary payload at or below 250 KiB.
2. WHEN Public_Artifact files have loaded from the CDN cache, THE Map_Application SHALL complete filter and country-selection state updates within 100 milliseconds under the documented Representative_Test_Profile.
3. WHEN measured at the 75th percentile under the documented representative mobile profile, THE Map_Application SHALL achieve Largest_Contentful_Paint within 2.5 seconds.
4. WHEN the overview loads, THE Map_Application SHALL defer Country_Detail shard loading until a Country_Detail is requested.
5. WHEN creator rows are delivered, THE Release_Manager SHALL partition creator rows according to the configured page-size policy rather than include every creator row in the default overview payload.
6. IF a requested Public_Artifact is missing or fails Artifact_Digest verification, THEN THE Map_Application SHALL retry according to the configured delivery-retry policy, display a loading state while configured attempts remain, and display a recoverable error with methodology access after configured attempts are exhausted.
7. IF a requested Public_Artifact is missing, corrupt, or unverifiable after configured retries, THEN THE Map_Application SHALL preserve an unknown or error state without presenting zero, an Empty_State, stale detail, or partial data as the requested valid value.
8. WHEN recovery from a Public_Artifact failure succeeds, THE Map_Application SHALL render data only after Artifact_Digest verification and SHALL clear the corresponding recoverable error state.
9. WHILE a newer release cannot be activated or delivered completely, THE Release_Manager SHALL keep the previous Verified_Release available through the Active_Release_Pointer.
10. WHEN immutable Public_Artifact files are published, THE Release_Manager SHALL apply immutable cache versioning and SHALL keep the Active_Release_Pointer separately refreshable.
11. IF the active Release_Manifest cannot be refreshed, THEN THE Map_Application SHALL preserve the last verified release state and SHALL identify the refresh failure without combining artifacts from different releases.

### Requirement 15: Protect Restricted Data and Administrative Operations

**User Story:** As a security administrator, I want restricted processing data and credentials separated from public delivery, so that publication does not expose secrets or record-level provenance.

#### Acceptance Criteria

1. THE System SHALL store API credentials only in the managed secret store approved by the Approved_Security_Policy.
2. WHEN a workload accesses an API credential, THE System SHALL authenticate the workload through an approved least-privilege identity and SHALL exclude the credential from persistent application state.
3. IF a build artifact, source file, browser bundle, log entry, Release_Manifest, or Public_Artifact contains an API credential, THEN THE System SHALL fail the affected build or publication gate and SHALL prevent activation.
4. THE System SHALL authorize access to Restricted_Data by authenticated role and resource class according to the Approved_Security_Policy.
5. IF an authenticated actor lacks authorization for requested Restricted_Data, THEN THE System SHALL deny the request without returning the Restricted_Data or a derived record-level value.
6. THE System SHALL encrypt Restricted_Data in transit and at rest according to the Approved_Security_Policy.
7. WHEN the Source_Adapter processes untrusted dataset content, THE Source_Adapter SHALL enforce the approved schema, configured field-size limits, configured record-size limits, path-traversal rejection, formula non-execution, and configured archive-decompression bounds.
8. IF untrusted content exceeds an Approved_Security_Policy limit or violates a parsing restriction, THEN THE Source_Adapter SHALL quarantine the affected input, record a non-sensitive reason, and prevent the affected content from reaching extraction outputs.
9. WHEN untrusted content satisfies every applicable Approved_Security_Policy limit and parsing restriction, THE Source_Adapter SHALL continue normal validation without quarantining the content as a precautionary action.
10. WHEN the Enrichment_Service or Source_Adapter initiates outbound access, THE System SHALL permit connections only to endpoints allowlisted by the Approved_Security_Policy for the approved metadata API and object storage.
11. IF a processing workload requests an outbound destination absent from the approved egress allowlist, THEN THE System SHALL deny the connection and SHALL record the denied destination class without recording credentials or Restricted_Data.
12. THE Enrichment_Service SHALL perform metadata enrichment without downloading source video media, transcripts, thumbnails, or dataset media.
13. THE Map_Application SHALL enforce the restrictive Content Security Policy, strict transport security, and applicable Subresource Integrity rules defined by the Approved_Security_Policy.
14. WHEN release approval is requested, THE System SHALL verify pinned dependency versions, lockfile integrity, license review status, and completion of the approved known-vulnerability scan for application and pipeline dependencies.
15. IF dependency scanning finds a vulnerability prohibited by the Approved_Security_Policy or scanning cannot complete, THEN THE Release_Manager SHALL reject the Release_Candidate without changing the Active_Release_Pointer.
16. WHEN an Administrative_Operation is invoked, THE System SHALL require successful authentication, authorization, and configured operation-specific rate-limit validation before changing protected state.
17. IF authentication fails for an Administrative_Operation regardless of any authorization result, THEN THE System SHALL deny the Administrative_Operation without changing protected state.
18. IF authorization or rate-limit validation for an authenticated Administrative_Operation fails, THEN THE System SHALL deny the Administrative_Operation without changing protected state.
19. THE System SHALL expose no anonymous public mutation operation for curator, release, policy, security, or Restricted_Data state.
20. WHEN access to Restricted_Data or an Administrative_Operation occurs, THE System SHALL durably record the authenticated actor, action, resource class, timestamp, and outcome in the Audit_Log.
21. IF a required Audit_Log record cannot be written durably, THEN THE System SHALL deny or roll back the associated Restricted_Data access or Administrative_Operation and SHALL produce a non-sensitive operator alert.
22. WHEN the Audit_Log is queried or exported, THE System SHALL require authorization defined by the Approved_Security_Policy and SHALL record the audit-access event in the Audit_Log.