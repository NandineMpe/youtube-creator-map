import type { Metadata } from "next";

import {
  BOUNDARY_STATEMENT,
  COMPARISON_STATEMENT,
  COUNTRY_STATEMENT,
  COUNT_DEFINITIONS,
  COVERAGE_STATEMENT,
  CORRECTION_PATH,
  LIMITATIONS,
  MEMBERSHIP_STATEMENT,
  METHODOLOGY_VERSION,
  OVERLAP_STATEMENT,
} from "../../lib/methodology";

export const metadata: Metadata = {
  title: "Methodology and limitations",
  description:
    "What the counts on this map mean, how they were produced, and what " +
    "they cannot tell you.",
};

/**
 * Methodology, limitations, and the correction path.
 *
 * Requirement 12.9 requires this to be reachable from the overview, every
 * country view, every empty state, and every recoverable error. The footer
 * link in the layout satisfies that structurally, so no individual view has
 * to remember.
 *
 * Requirement refs: 1.9, 7.9, 12.1-12.11
 */
export default function MethodologyPage() {
  return (
    <article>
      <h2>Methodology and limitations</h2>
      <p className="release-context">
        <span>
          Methodology version{" "}
          <span className="release-context__value">{METHODOLOGY_VERSION}</span>
        </span>
      </p>

      <section aria-labelledby="what-this-shows">
        <h3 id="what-this-shows">What this shows</h3>
        <p>{MEMBERSHIP_STATEMENT}</p>
        <p>{COUNTRY_STATEMENT}</p>
      </section>

      <section aria-labelledby="counting">
        <h3 id="counting">How things are counted</h3>
        <dl className="definition-list">
          {COUNT_DEFINITIONS.map((entry) => (
            <div key={entry.term}>
              <dt>{entry.term}</dt>
              <dd>{entry.definition}</dd>
            </div>
          ))}
        </dl>
        <div className="callout">
          <p>{OVERLAP_STATEMENT}</p>
        </div>
      </section>

      <section aria-labelledby="coverage">
        <h3 id="coverage">Coverage</h3>
        <p>{COVERAGE_STATEMENT}</p>
      </section>

      <section aria-labelledby="corpora">
        <h3 id="corpora">Candidate and comparison corpora</h3>
        <p>{COMPARISON_STATEMENT}</p>
      </section>

      <section aria-labelledby="boundaries">
        <h3 id="boundaries">Map boundaries</h3>
        <p>{BOUNDARY_STATEMENT}</p>
      </section>

      <section aria-labelledby="limitations">
        <h3 id="limitations">Limitations</h3>
        <ul>
          {LIMITATIONS.map((limitation) => (
            <li key={limitation}>{limitation}</li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="corrections">
        <h3 id="corrections">{CORRECTION_PATH.heading}</h3>
        <p>{CORRECTION_PATH.body}</p>
      </section>
    </article>
  );
}
