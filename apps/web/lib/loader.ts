/**
 * Verified artifact loading.
 *
 * Every payload is digest-verified before it is parsed, and every parse is
 * schema-validated before it reaches a component. Requirement 14.7 is the
 * strict half of this: when an artifact is missing, corrupt, or
 * unverifiable, the loader must surface an error state rather than a zero,
 * an empty state, or stale data — because a zero rendered as a valid value
 * is indistinguishable from a real zero, and that is a wrong answer rather
 * than a failure.
 *
 * Requirement refs: 8.8, 10.10, 14.6-14.11
 */

import {
  activeReleasePointer,
  countryDetail,
  overviewArtifact,
  releaseManifest,
  type ActiveReleasePointer,
  type CountryDetail,
  type OverviewArtifact,
  type ReleaseManifest,
} from "@creator-map/shared-schemas";
import type { z } from "zod";

/** Why a load failed. Each maps to a distinct user-facing state. */
export type LoadFailureKind =
  | "network"
  | "not-found"
  | "digest-mismatch"
  | "schema-invalid"
  | "mixed-release"
  | "pointer-refresh";

export class ArtifactLoadError extends Error {
  readonly kind: LoadFailureKind;
  readonly path: string;
  readonly attempts: number;

  constructor(
    kind: LoadFailureKind,
    path: string,
    message: string,
    attempts = 1,
  ) {
    super(message);
    this.name = "ArtifactLoadError";
    this.kind = kind;
    this.path = path;
    this.attempts = attempts;
  }

  /**
   * Whether retrying could plausibly succeed.
   *
   * A digest mismatch is not retryable in the useful sense: the bytes at
   * that path do not match what the manifest promised, and fetching them
   * again returns the same bytes. Treating it as transient would mask a
   * corrupted or substituted artifact.
   */
  get isTransient(): boolean {
    return this.kind === "network";
  }
}

export interface RetryPolicy {
  readonly maxAttempts: number;
  readonly baseDelayMs: number;
  readonly maxDelayMs: number;
}

export const DEFAULT_RETRY: RetryPolicy = {
  maxAttempts: 3,
  baseDelayMs: 250,
  maxDelayMs: 2000,
};

/** Compute the sha256 digest of raw bytes, in the manifest's format. */
export async function digestBytes(bytes: ArrayBuffer): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  const hex = Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `sha256:${hex}`;
}

export interface FetchOptions {
  readonly baseUrl?: string;
  readonly retry?: RetryPolicy;
  readonly signal?: AbortSignal;
  /** Injected for tests; defaults to global fetch. */
  readonly fetchImpl?: typeof fetch;
  /** Injected for tests; defaults to a real timer. */
  readonly sleep?: (ms: number) => Promise<void>;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function joinUrl(base: string, path: string): string {
  if (!base) return path;
  return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

/**
 * Fetch raw bytes with bounded retries.
 *
 * Only network failures are retried. A 404 is not a transient condition
 * for an immutable artifact: the release manifest named a path, and if it
 * is absent the release is incomplete, which retrying cannot fix.
 */
async function fetchBytes(
  path: string,
  options: FetchOptions,
): Promise<ArrayBuffer> {
  const retry = options.retry ?? DEFAULT_RETRY;
  const doFetch = options.fetchImpl ?? fetch;
  const pause = options.sleep ?? delay;
  const url = joinUrl(options.baseUrl ?? "", path);

  let lastError: unknown;

  for (let attempt = 1; attempt <= retry.maxAttempts; attempt += 1) {
    try {
      const response = await doFetch(url, { signal: options.signal });

      if (response.status === 404) {
        throw new ArtifactLoadError(
          "not-found",
          path,
          `artifact not found: ${path}`,
          attempt,
        );
      }
      if (!response.ok) {
        throw new ArtifactLoadError(
          "network",
          path,
          `fetch failed with status ${response.status}`,
          attempt,
        );
      }
      return await response.arrayBuffer();
    } catch (error) {
      if (error instanceof ArtifactLoadError && !error.isTransient) {
        throw error;
      }
      lastError = error;
      if (attempt < retry.maxAttempts) {
        const backoff = Math.min(
          retry.baseDelayMs * 2 ** (attempt - 1),
          retry.maxDelayMs,
        );
        await pause(backoff);
      }
    }
  }

  const message =
    lastError instanceof Error ? lastError.message : "unknown fetch failure";
  throw new ArtifactLoadError(
    "network",
    path,
    `${message} after ${retry.maxAttempts} attempts`,
    retry.maxAttempts,
  );
}

/**
 * Fetch, verify, and parse one artifact.
 *
 * The order matters: verify the bytes against the expected digest *before*
 * parsing, so a corrupted payload is reported as a digest failure rather
 * than as a confusing schema error.
 */
export async function loadVerified<T>(
  path: string,
  expectedDigest: string,
  schema: z.ZodType<T>,
  options: FetchOptions = {},
): Promise<T> {
  const bytes = await fetchBytes(path, options);

  const actual = await digestBytes(bytes);
  if (actual !== expectedDigest) {
    throw new ArtifactLoadError(
      "digest-mismatch",
      path,
      `digest mismatch for ${path}`,
    );
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new ArtifactLoadError("schema-invalid", path, `${path} is not JSON`);
  }

  const result = schema.safeParse(parsed);
  if (!result.success) {
    throw new ArtifactLoadError(
      "schema-invalid",
      path,
      `${path} does not match its schema: ${result.error.issues[0]?.message ?? "unknown"}`,
    );
  }
  return result.data;
}

/** A release whose pointer, manifest, and overview all verified together. */
export interface VerifiedRelease {
  readonly pointer: ActiveReleasePointer;
  readonly manifest: ReleaseManifest;
  readonly overview: OverviewArtifact;
}

/**
 * Load the active release.
 *
 * The pointer is fetched without a digest because nothing precedes it to
 * name one — it is the root of trust, and is the only artifact served with
 * a short cache lifetime (Requirement 14.10). Everything below it is
 * digest-verified against the manifest.
 */
export async function loadActiveRelease(
  options: FetchOptions = {},
): Promise<VerifiedRelease> {
  const pointerBytes = await fetchBytes("active-release.json", options);
  let pointerRaw: unknown;
  try {
    pointerRaw = JSON.parse(new TextDecoder().decode(pointerBytes));
  } catch {
    throw new ArtifactLoadError(
      "pointer-refresh",
      "active-release.json",
      "the active release pointer is not JSON",
    );
  }

  const pointerResult = activeReleasePointer.safeParse(pointerRaw);
  if (!pointerResult.success) {
    throw new ArtifactLoadError(
      "pointer-refresh",
      "active-release.json",
      "the active release pointer does not match its schema",
    );
  }
  const pointer = pointerResult.data;

  const manifest = await loadVerified(
    pointer.manifestPath,
    pointer.manifestDigest,
    releaseManifest,
    options,
  );

  // Requirement 14.11: artifacts from different releases must never be
  // combined. The pointer and manifest are independently fetched, so this
  // is the point where a stale cache could pair them incorrectly.
  if (manifest.releaseId !== pointer.releaseId) {
    throw new ArtifactLoadError(
      "mixed-release",
      pointer.manifestPath,
      `pointer names release ${pointer.releaseId} but the manifest is ${manifest.releaseId}`,
    );
  }

  const overviewPath = `releases/${manifest.releaseId}/overview.json`;
  const overviewDigest = manifest.artifactDigests[overviewPath];
  if (!overviewDigest) {
    throw new ArtifactLoadError(
      "not-found",
      overviewPath,
      "the manifest does not list an overview artifact",
    );
  }

  const overview = await loadVerified(
    overviewPath,
    overviewDigest,
    overviewArtifact,
    options,
  );

  if (overview.releaseId !== manifest.releaseId) {
    throw new ArtifactLoadError(
      "mixed-release",
      overviewPath,
      "the overview belongs to a different release than the manifest",
    );
  }

  return { pointer, manifest, overview };
}

/**
 * Load one country's detail shard.
 *
 * Requirement 14.4 defers this until a country is actually requested, so
 * it is deliberately not part of `loadActiveRelease`.
 */
export async function loadCountryDetail(
  manifest: ReleaseManifest,
  country: string,
  options: FetchOptions = {},
): Promise<CountryDetail> {
  const path = `releases/${manifest.releaseId}/countries/${country}.json`;
  const expected = manifest.artifactDigests[path];

  if (!expected) {
    throw new ArtifactLoadError(
      "not-found",
      path,
      `the manifest lists no shard for ${country}`,
    );
  }

  const detail = await loadVerified(path, expected, countryDetail, options);

  if (detail.country !== country) {
    throw new ArtifactLoadError(
      "mixed-release",
      path,
      `shard for ${country} reports country ${detail.country}`,
    );
  }
  return detail;
}

/** The states a view can be in. Loading, empty, and error are distinct. */
export type LoadState<T> =
  | { readonly status: "loading" }
  | { readonly status: "ready"; readonly data: T }
  | { readonly status: "error"; readonly error: ArtifactLoadError };

/**
 * Human-readable copy for a failure.
 *
 * Deliberately never says "no data" — Requirement 14.7 separates a failure
 * from an empty result, and conflating them would present an outage as a
 * finding.
 */
export function describeFailure(error: ArtifactLoadError): string {
  switch (error.kind) {
    case "network":
      return "Could not reach the data for this view. It may be a temporary network problem.";
    case "not-found":
      return "Part of this release is missing. The figures cannot be shown accurately.";
    case "digest-mismatch":
      return "This data did not match its published checksum, so it was not used.";
    case "schema-invalid":
      return "This data was not in the expected format, so it was not used.";
    case "mixed-release":
      return "Data from two different releases arrived together and was rejected.";
    case "pointer-refresh":
      return "Could not confirm which release is current. The last verified view is still shown.";
  }
}
