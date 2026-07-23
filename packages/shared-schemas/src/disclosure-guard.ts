/**
 * Recursive prohibited-content inspection for public artifacts.
 *
 * Requirement 7.6 requires keys, values, embedded metadata, and generated
 * indexes to be inspected recursively for prohibited identifiers and fields;
 * Requirement 7.7 requires anything undeterminable to be treated as
 * prohibited. This module is the shared implementation of that rule, used by
 * the release validator before publication and available to the browser
 * loader as defence in depth.
 *
 * It is intentionally independent of the Zod schemas. The schemas reject
 * artifacts whose *shape* is wrong; this catches a prohibited value smuggled
 * inside an otherwise well-shaped string field.
 *
 * Requirement refs: 7.3, 7.5, 7.6, 7.7, 15.3
 */

/** A single prohibited-content finding. */
export interface DisclosureFinding {
  /** JSON path to the offending node, e.g. `countries[3].sourceLocator`. */
  readonly path: string;
  /** Why the node is prohibited. Safe to log: carries no restricted value. */
  readonly reason: string;
}

/**
 * A bare YouTube video ID: exactly 11 chars from the base64url alphabet.
 *
 * Requirement 7.5 forbids raw video identifiers in public artifacts. The
 * pattern requires a non-word boundary on both sides so ordinary prose and
 * longer tokens do not trip it.
 */
const RAW_VIDEO_ID = /(?<![\w-])[A-Za-z0-9_-]{11}(?![\w-])/;

/**
 * A value that is *entirely* a bare video ID, with no surrounding text.
 *
 * Used for prose fields, where a substring match would flag ordinary words.
 * Requires at least one digit, underscore, or hyphen, or mixed case: real
 * video IDs are effectively never lowercase dictionary words, whereas an
 * 11-letter English word ("presentation") frequently is.
 */
const BARE_VIDEO_ID_ONLY =
  /^(?=[A-Za-z0-9_-]{11}$)(?:.*[0-9_-]|.*[a-z].*[A-Z]|.*[A-Z].*[a-z]).*$/;

/** A raw YouTube channel ID: "UC" followed by 22 base64url characters. */
const RAW_CHANNEL_ID = /(?<![\w-])UC[A-Za-z0-9_-]{22}(?![\w-])/;

/** A YouTube URL in any form that embeds an identifier. */
const YOUTUBE_URL = /(?:youtube\.com|youtu\.be)\//i;

/** Field names that must never appear on a public artifact, at any depth. */
const PROHIBITED_KEYS: ReadonlyArray<readonly [RegExp, string]> = [
  [/^videoIds?$/i, "raw video identifier field"],
  [/^rawVideoIds?$/i, "raw video identifier field"],
  [/sourceLocator/i, "source locator field"],
  [/^channelId$/i, "raw channel identifier field (use publicChannelKey)"],
  [/rawResponse/i, "raw API response field"],
  [/responseDigest/i, "restricted provenance join"],
  [/^email$/i, "contact field"],
  [/contactEmail/i, "contact field"],
  [/^phone/i, "contact field"],
  [/suppressionReason/i, "suppression reason must not be exposed"],
  [/restricted_?/i, "restricted-marked field"],
  [/apiKey/i, "credential field"],
  [/^secret/i, "credential field"],
  [/password/i, "credential field"],
  [/accessToken/i, "credential field"],
];

/** Value patterns prohibited regardless of the field they appear under. */
const PROHIBITED_VALUES: ReadonlyArray<readonly [RegExp, string]> = [
  [RAW_CHANNEL_ID, "raw YouTube channel identifier"],
  [YOUTUBE_URL, "YouTube URL embedding an identifier"],
  [/\bsb_secret_[A-Za-z0-9_-]+/, "Supabase secret key"],
  [/\bAIza[A-Za-z0-9_-]{35}\b/, "Google API key"],
  [/postgres(?:ql)?:\/\/[^\s]*:[^\s]*@/i, "database connection string"],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, "private key material"],
];

/**
 * Free-text fields that legitimately contain prose.
 *
 * The bare-video-ID shape (11 base64url characters) is indistinguishable
 * from an ordinary English word: "identifiers" and "presentation" both
 * match. Scanning prose for it produces false positives that would block
 * valid releases, and Requirement 12 obliges us to publish explanatory
 * prose. So a whole-string match is required for these fields rather than a
 * substring match — a field whose *entire* value is a bare identifier is
 * still caught, while a sentence that happens to contain an 11-letter word
 * is not.
 *
 * Every other pattern (channel IDs, YouTube URLs, credentials) still applies
 * to these fields, because none of those can occur innocently in prose.
 */
const PROSE_KEYS =
  /^(?:note|notes|description|summary|methodology|label|title|displayName|attribution|license|datasetName|disputedTerritoryTreatment|disclaimer|caption|heading|text|message|reason)$/i;

/**
 * Fields whose values are legitimately identifier-shaped tokens.
 *
 * These are exempt from the bare-video-ID scan only; they remain subject to
 * every key check and to the channel-ID and credential patterns.
 */
const VIDEO_ID_EXEMPT_KEYS =
  /^(?:releaseId|version|schemaVersion|methodologyVersion|disclosurePolicyVersion|sourceCitation|nextCursor|publicChannelKey|manifestPath|datasetId)$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function checkKey(
  key: string,
  path: string,
  findings: DisclosureFinding[],
): void {
  for (const [pattern, reason] of PROHIBITED_KEYS) {
    if (pattern.test(key)) {
      findings.push({ path, reason });
      return;
    }
  }
}

function checkStringValue(
  value: string,
  key: string | null,
  path: string,
  findings: DisclosureFinding[],
): void {
  for (const [pattern, reason] of PROHIBITED_VALUES) {
    if (pattern.test(value)) {
      findings.push({ path, reason });
      return;
    }
  }

  // A digest is a legitimate 64-hex value; exempt it from the video-ID scan
  // rather than from the credential scans above.
  if (/^sha256:[a-f0-9]{64}$/.test(value)) return;

  if (key !== null && VIDEO_ID_EXEMPT_KEYS.test(key)) return;

  // Under a prose key, only a value that is *entirely* a bare identifier is
  // prohibited; an 11-letter word inside a sentence is not.
  if (key !== null && PROSE_KEYS.test(key)) {
    if (BARE_VIDEO_ID_ONLY.test(value)) {
      findings.push({ path, reason: "possible raw YouTube video identifier" });
    }
    return;
  }

  if (RAW_VIDEO_ID.test(value)) {
    findings.push({ path, reason: "possible raw YouTube video identifier" });
  }
}

/**
 * Recursively inspect a parsed artifact for prohibited content.
 *
 * Returns every finding rather than throwing on the first, so a release
 * report can list all problems in one pass.
 */
export function findProhibitedContent(
  node: unknown,
  path = "$",
  findings: DisclosureFinding[] = [],
): DisclosureFinding[] {
  if (typeof node === "string") {
    checkStringValue(node, null, path, findings);
    return findings;
  }

  if (Array.isArray(node)) {
    node.forEach((item, index) => {
      findProhibitedContent(item, `${path}[${index}]`, findings);
    });
    return findings;
  }

  if (isRecord(node)) {
    for (const [key, value] of Object.entries(node)) {
      const childPath = path === "$" ? key : `${path}.${key}`;
      checkKey(key, childPath, findings);

      if (typeof value === "string") {
        checkStringValue(value, key, childPath, findings);
      } else {
        findProhibitedContent(value, childPath, findings);
      }
    }
    return findings;
  }

  // Numbers, booleans, null: nothing to inspect.
  return findings;
}

/** Thrown when an artifact fails disclosure inspection. */
export class DisclosureViolationError extends Error {
  readonly findings: readonly DisclosureFinding[];

  constructor(findings: readonly DisclosureFinding[]) {
    const summary = findings
      .slice(0, 5)
      .map((f) => `${f.path}: ${f.reason}`)
      .join("; ");
    const suffix = findings.length > 5 ? ` (+${findings.length - 5} more)` : "";
    super(`artifact failed disclosure inspection: ${summary}${suffix}`);
    this.name = "DisclosureViolationError";
    this.findings = findings;
  }
}

/**
 * Assert an artifact carries no prohibited content.
 *
 * Fails closed: any finding rejects the whole artifact (Requirement 7.7).
 */
export function assertNoProhibitedContent(node: unknown): void {
  const findings = findProhibitedContent(node);
  if (findings.length > 0) {
    throw new DisclosureViolationError(findings);
  }
}
