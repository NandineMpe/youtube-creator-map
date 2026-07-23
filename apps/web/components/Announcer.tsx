"use client";

import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";

/**
 * A single coordinated live region.
 *
 * Requirement 13.3 requires a filter change to announce the resulting
 * filter, loading status, completion status, and summary update. Doing
 * that with a `role="status"` on each affected component produces several
 * regions updating at once, and screen readers queue or drop them
 * unpredictably — the user hears fragments in an arbitrary order, or
 * nothing.
 *
 * One region, written to deliberately, gives one announcement per action
 * in the order the action actually happened.
 *
 * Two politeness levels, because they are genuinely different: progress
 * and results are polite and may wait for a pause in speech; errors are
 * assertive and interrupt, since a stale figure being read aloud after a
 * failure is worse than the interruption.
 *
 * Requirement refs: 13.3, 13.9
 */

interface AnnouncerApi {
  /** Announce progress or a result. Waits for a pause in speech. */
  readonly announce: (message: string) => void;
  /** Announce a failure. Interrupts. */
  readonly alert: (message: string) => void;
}

const AnnouncerContext = createContext<AnnouncerApi | null>(null);

export function AnnouncerProvider({ children }: { children: ReactNode }) {
  const [polite, setPolite] = useState("");
  const [assertive, setAssertive] = useState("");
  const lastPolite = useRef("");

  // Zero-width space, written as an escape rather than the literal
  // character: an invisible byte in source is a maintenance hazard and
  // lint rightly objects to it.
  const ZERO_WIDTH = "\u200B";

  const announce = useCallback((message: string) => {
    // An identical string written twice is not re-announced by most
    // screen readers, so repeating an action would be silent. Appending
    // a zero-width space makes the text differ without changing what is
    // spoken.
    setPolite(
      message === lastPolite.current ? `${message}${ZERO_WIDTH}` : message,
    );
    lastPolite.current = message;
  }, []);

  const alert = useCallback((message: string) => {
    setAssertive(message);
  }, []);

  return (
    <AnnouncerContext.Provider value={{ announce, alert }}>
      {children}
      {/*
        Both regions are always present in the DOM. A region added at the
        moment it has content is frequently missed, because the screen
        reader has not been observing it.
      */}
      <div
        className="visually-hidden"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {polite}
      </div>
      <div
        className="visually-hidden"
        role="alert"
        aria-live="assertive"
        aria-atomic="true"
      >
        {assertive}
      </div>
    </AnnouncerContext.Provider>
  );
}

/**
 * Access the announcer.
 *
 * Returns no-ops outside a provider so a component can announce without
 * knowing whether one is mounted — a missing provider should not crash a
 * view, only make it quieter.
 */
export function useAnnouncer(): AnnouncerApi {
  const context = useContext(AnnouncerContext);
  return (
    context ?? {
      announce: () => undefined,
      alert: () => undefined,
    }
  );
}

/**
 * Phrase a filter change for announcement.
 *
 * Requirement 13.3 names four things that must be conveyed. Ordering them
 * filter, then counts, then coverage matches what a sighted user reads
 * top to bottom, so the two experiences describe the same page in the
 * same sequence.
 */
export function describeFilterChange(options: {
  readonly datasetLabel: string;
  readonly creators: number;
  readonly videos: number;
  readonly countries: number;
  readonly exact: boolean;
}): string {
  const counts = new Intl.NumberFormat("en");

  const base =
    `Showing ${options.datasetLabel}. ` +
    `${counts.format(options.creators)} creators, ` +
    `${counts.format(options.videos)} represented videos, ` +
    `across ${counts.format(options.countries)} country ` +
    `${options.countries === 1 ? "bucket" : "buckets"}.`;

  return options.exact
    ? base
    : `${base} These are the full release figures; this release does not publish that exact combination.`;
}
