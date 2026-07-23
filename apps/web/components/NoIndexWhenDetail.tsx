"use client";

import { useEffect } from "react";

/**
 * Apply search-engine exclusion while creator detail is on screen.
 *
 * Requirement 7.10 requires creator-detail routes to carry exclusion
 * metadata. Country selection lives in a query parameter rather than a
 * path, so there is no separate route whose static metadata could carry
 * it — the directive has to be applied when the detail actually appears.
 *
 * `noindex` is paired with `noarchive` deliberately: without it a cached
 * copy can outlive both the removal of the page from the index and a
 * creator's opt-out, which would defeat the correction path Requirement
 * 7.9 promises.
 *
 * A crawler that does not execute JavaScript never sees the detail
 * either, since the rows are fetched client-side. This covers the ones
 * that do.
 *
 * Requirement refs: 7.3, 7.9, 7.10
 */
export function NoIndexWhenDetail({ active }: { readonly active: boolean }) {
  useEffect(() => {
    if (!active) return;

    const tag = document.createElement("meta");
    tag.name = "robots";
    tag.content = "noindex, nofollow, noarchive";
    document.head.appendChild(tag);

    return () => {
      tag.remove();
    };
  }, [active]);

  return null;
}
