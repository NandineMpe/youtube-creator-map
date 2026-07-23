import Link from "next/link";

import { type ArtifactLoadError, describeFailure } from "../lib/loader";

/**
 * Loading, empty, and error presentations.
 *
 * These are three separate components on purpose. Requirement 6.11 and
 * 14.7 require an empty result, a delivery failure, and a digest failure
 * to be distinguishable — a shared "nothing here" component would collapse
 * them, and a visitor could not tell whether a country genuinely has no
 * creators or whether the shard failed to load. The first is a finding;
 * the second is an outage; presenting them alike misreports one of them.
 *
 * Requirement refs: 6.11, 9.7, 10.10, 10.11, 12.9, 13.3, 14.6-14.8
 */

export function LoadingPanel({ label }: { readonly label: string }) {
  return (
    <div
      className="state-panel state-panel--loading"
      role="status"
      aria-live="polite"
    >
      <h2>Loading</h2>
      <p>{label}</p>
    </div>
  );
}

/**
 * A valid result that contains nothing.
 *
 * Deliberately says so explicitly: "this filter matched no records" is a
 * different claim from "we could not load the records", and the copy has
 * to make that unambiguous (Requirement 6.11).
 */
export function EmptyPanel({
  heading,
  body,
}: {
  readonly heading: string;
  readonly body: string;
}) {
  return (
    <div className="state-panel state-panel--empty" role="status">
      <h2>{heading}</h2>
      <p>{body}</p>
      <p>
        This is a complete result, not a loading or delivery problem.{" "}
        <Link href="/methodology">How coverage is measured</Link>.
      </p>
    </div>
  );
}

/**
 * A recoverable failure.
 *
 * Requirement 14.7 forbids presenting zero, an empty state, stale detail,
 * or partial data as the requested value. So this component renders no
 * figures at all — there is nothing trustworthy to show, and a number
 * beside an error message would be read as data.
 */
export function ErrorPanel({
  error,
  onRetry,
}: {
  readonly error: ArtifactLoadError;
  readonly onRetry?: () => void;
}) {
  return (
    <div className="state-panel state-panel--error" role="alert">
      <h2>This view could not be shown</h2>
      <p>{describeFailure(error)}</p>
      <p>No figures are shown for this view, because none could be verified.</p>
      {onRetry && (
        <p>
          <button type="button" onClick={onRetry}>
            Try again
          </button>
        </p>
      )}
      <p>
        {/* Requirement 12.9: methodology stays reachable from error
            states, not only from working ones. */}
        <Link href="/methodology">Methodology and limitations</Link>
      </p>
    </div>
  );
}

/**
 * Report which URL components were corrected.
 *
 * Requirement 11.9 requires each corrected component to be identified, so
 * a visitor following a stale or hand-edited link understands why the view
 * differs from what they asked for.
 */
export function CorrectionNotice({
  corrections,
}: {
  readonly corrections: readonly {
    field: string;
    rejected: string;
    reason: string;
  }[];
}) {
  if (corrections.length === 0) return null;

  return (
    <div className="state-panel state-panel--empty" role="status">
      <h2>Some parts of that link could not be used</h2>
      <ul>
        {corrections.map((correction) => (
          <li key={`${correction.field}:${correction.rejected}`}>
            <strong>{correction.field}</strong>: {correction.reason}
          </li>
        ))}
      </ul>
      <p>The rest of the view was loaded normally.</p>
    </div>
  );
}
