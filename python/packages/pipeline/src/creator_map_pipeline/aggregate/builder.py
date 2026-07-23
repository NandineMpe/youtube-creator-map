"""Deterministic exact aggregation.

Every published count is an exact distinct-set cardinality, never an
additive approximation (Requirement 5.12). The distinction that matters
throughout: a *source occurrence* is one retained row, clip, or timestamp,
while a *represented video* is one distinct canonical identifier. Duplicate
evidence raises the former and never the latter (Requirement 5.2).

Aggregation runs against the provenance store through SQL rather than in
Python, so a 50-million-row occurrence table never has to be materialised.
The queries are written to be engine-portable: they use only standard SQL
so the same statements serve PostgreSQL directly and DuckDB over exported
Parquet.

Requirement refs: 5.1-5.13, 6.1-6.6

Note on S608: the queries below compose module-level CTE constants into
statement text with f-strings. No caller-supplied value reaches the SQL that
way — every filter value, cutoff, and identity is bound as a parameter, and
the one numeric interpolation (`limit`) passes through `int()` first. The
rule is suppressed per statement rather than globally so a future query that
does interpolate user input is still flagged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import psycopg
from creator_map_schemas import (
    UNKNOWN_COUNTRY,
    CorpusClass,
    CountrySummary,
    CoverageSummary,
    Filter,
    VideoResolutionPartition,
)


@dataclass(frozen=True, slots=True)
class AggregateInputs:
    """Pinned inputs defining exactly what a build may read.

    Pinning is what makes a release reproducible (Requirement 5.13): the
    same inputs, filter, and cutoff must yield byte-identical aggregates on
    every build.
    """

    enrichment_cutoff: datetime
    policy_version: str
    active_filter: Filter
    #: Channel observations may come from a different policy than video
    #: ones: resolving video-to-channel and resolving declared country are
    #: separate enrichment policies with separate work identities, and a
    #: release can legitimately pin one of each. Defaults to
    #: `policy_version` so a single-policy release needs no extra
    #: configuration.
    channel_policy_version: str | None = None

    @property
    def channel_policy(self) -> str:
        return self.channel_policy_version or self.policy_version


@dataclass(frozen=True, slots=True)
class CreatorAggregate:
    """One channel's contribution, before disclosure is applied."""

    channel_id: str
    display_name: str | None
    country: str
    represented_video_count: int
    dataset_breakdown: tuple[tuple[str, int], ...]


@dataclass(slots=True)
class AggregateResult:
    """Everything one aggregation run produced."""

    countries: list[CountrySummary] = field(default_factory=list)
    creators: list[CreatorAggregate] = field(default_factory=list)
    coverage: CoverageSummary | None = None
    partition: VideoResolutionPartition | None = None
    represented_video_count: int = 0
    creator_count: int = 0

    @property
    def represented_country_count(self) -> int:
        """Countries with at least one represented video, excluding Unknown.

        Unknown is a bucket, not a country: Requirement 6.8 places it
        outside the geographic choropleth, so counting it here would
        overstate geographic coverage.
        """
        return sum(
            1
            for summary in self.countries
            if summary.country != UNKNOWN_COUNTRY and summary.represented_video_count > 0
        )


#: Selects the single eligible observation per video at the pinned cutoff.
#:
#: Requirement 3.11 needs a total order: observed_at alone is not, because
#: two observations can share an instant, so response_digest breaks ties.
#: DISTINCT ON with a matching ORDER BY is how PostgreSQL expresses
#: "one row per identity" without a window-function subquery.
_SELECTED_VIDEO_CTE = """
selected_video as (
    select distinct on (video_id)
        video_id, status, channel_id
    from enrichment.video_observation
    where policy_version = %(policy_version)s
      and observed_at <= %(cutoff)s
    order by video_id, observed_at desc, response_digest desc
)
"""

_SELECTED_CHANNEL_CTE = """
selected_channel as (
    select distinct on (channel_id)
        channel_id, status, display_name, declared_country
    from enrichment.channel_observation
    where policy_version = %(channel_policy_version)s
      and observed_at <= %(cutoff)s
    order by channel_id, observed_at desc, response_digest desc
)
"""

#: Occurrences admitted by the active filter.
#:
#: Requirement 5.8 admits an occurrence only when both its dataset and its
#: corpus class are included; 5.9 excludes it when either is excluded. The
#: join to dataset_contract is what enforces the corpus-class half.
_FILTERED_OCCURRENCE_CTE = """
filtered_occurrence as (
    select o.dataset_id, o.dataset_version, o.video_id
    from provenance.normalized_occurrence o
    join provenance.dataset_contract c
      on c.dataset_id = o.dataset_id
     and c.dataset_version = o.dataset_version
    where o.dataset_id = any(%(datasets)s)
      and c.corpus_class = any(%(corpus_classes)s)
)
"""


def _filter_params(inputs: AggregateInputs) -> dict[str, object]:
    return {
        "policy_version": inputs.policy_version,
        "channel_policy_version": inputs.channel_policy,
        "cutoff": inputs.enrichment_cutoff,
        "datasets": list(inputs.active_filter.datasets),
        "corpus_classes": [c.value for c in inputs.active_filter.corpus_classes],
    }


def compute_headline_counts(
    cur: psycopg.Cursor[tuple[object, ...]], inputs: AggregateInputs
) -> tuple[int, int]:
    """Return (source occurrence count, distinct represented video count).

    These two numbers are the whole point of the counting-unit distinction:
    they differ precisely by the retained duplicate evidence.
    """
    cur.execute(
        f"with {_FILTERED_OCCURRENCE_CTE} "
        "select count(*), count(distinct video_id) from filtered_occurrence",
        _filter_params(inputs),
    )
    row = cur.fetchone()
    if row is None:
        return 0, 0
    return int(str(row[0])), int(str(row[1]))


def compute_country_summaries(
    cur: psycopg.Cursor[tuple[object, ...]], inputs: AggregateInputs
) -> list[CountrySummary]:
    """Compute per-country aggregates with exact distinct-set semantics.

    A video reaches a country bucket only through a resolved channel with a
    selected observation (Requirement 5.5). A channel whose selected
    observation carries no supported country lands in Unknown
    (Requirement 5.7), never a guess.
    """
    cur.execute(
        f"""
        with {_FILTERED_OCCURRENCE_CTE},
             {_SELECTED_VIDEO_CTE},
             {_SELECTED_CHANNEL_CTE},
        attributed as (
            -- DISTINCT is load-bearing: filtered_occurrence holds one row
            -- per retained occurrence, so a video with several occurrences
            -- would appear several times here. Joining that back to the
            -- occurrence table would then square the duplicates and report
            -- a per-country subtotal larger than the whole corpus.
            select distinct
                f.video_id,
                sv.channel_id,
                coalesce(sc.declared_country, %(unknown)s) as country
            from filtered_occurrence f
            join selected_video sv on sv.video_id = f.video_id
            join selected_channel sc on sc.channel_id = sv.channel_id
            where sv.status = 'Resolved'
              and sc.status = 'Resolved'
        ),
        -- Occurrences are joined back to their country in one pass rather
        -- than recounted per country by a correlated subquery, which would
        -- rescan the occurrence table once for every bucket.
        occurrence_country as (
            select a.country, count(*) as source_occurrence_count
            from filtered_occurrence f
            join attributed a on a.video_id = f.video_id
            group by a.country
        )
        select
            a.country,
            count(distinct a.channel_id) as creator_count,
            count(distinct a.video_id) as represented_video_count,
            coalesce(max(oc.source_occurrence_count), 0) as source_occurrence_count,
            count(distinct a.video_id) as resolved_video_count
        from attributed a
        left join occurrence_country oc on oc.country = a.country
        group by a.country
        order by a.country
        """,
        {**_filter_params(inputs), "unknown": UNKNOWN_COUNTRY},
    )

    summaries: list[CountrySummary] = []
    for row in cur.fetchall():
        summaries.append(
            CountrySummary(
                country=str(row[0]),
                creator_count=int(str(row[1])),
                represented_video_count=int(str(row[2])),
                source_occurrence_count=int(str(row[3])),
                resolved_video_count=int(str(row[4])),
                # Unresolved videos carry no country attribution, so they
                # never appear in a country bucket (Requirement 5.6).
                unavailable_video_count=0,
            )
        )
    return summaries


def compute_partition(
    cur: psycopg.Cursor[tuple[object, ...]], inputs: AggregateInputs
) -> VideoResolutionPartition:
    """Assign every distinct filtered video to exactly one state.

    Requirement 6.2-6.4: the five states are disjoint and exhaustive, and
    their counts sum to the distinct input video count. The CASE ladder is
    ordered so each video matches exactly one arm, and the model's own
    validator refuses a partition that does not reconcile.
    """
    cur.execute(
        f"""
        with {_FILTERED_OCCURRENCE_CTE},
             {_SELECTED_VIDEO_CTE},
        distinct_video as (
            select distinct video_id from filtered_occurrence
        ),
        classified as (
            select
                d.video_id,
                case
                    when sv.status = 'Resolved' then 'resolved'
                    when sv.status = 'Invalid' then 'invalid'
                    when sv.status = 'UnavailableUnclassified'
                        then 'unavailable'
                    when w.state = 'TerminalFailure' then 'terminal'
                    else 'pending'
                end as bucket
            from distinct_video d
            left join selected_video sv on sv.video_id = d.video_id
            left join enrichment.work_item w
              on w.entity_id = d.video_id
             and w.entity_kind = 'Video'
             and w.policy_version = %(policy_version)s
        )
        select bucket, count(*) from classified group by bucket
        """,
        _filter_params(inputs),
    )
    counts = {str(row[0]): int(str(row[1])) for row in cur.fetchall()}

    total = sum(counts.values())
    return VideoResolutionPartition(
        distinct_input_video_count=total,
        resolved_count=counts.get("resolved", 0),
        unavailable_unclassified_count=counts.get("unavailable", 0),
        retryable_or_pending_count=counts.get("pending", 0),
        invalid_count=counts.get("invalid", 0),
        terminal_failure_count=counts.get("terminal", 0),
    )


def compute_coverage(
    cur: psycopg.Cursor[tuple[object, ...]],
    inputs: AggregateInputs,
    *,
    occurrence_count: int,
    partition: VideoResolutionPartition,
) -> CoverageSummary:
    """Compute channel coverage alongside the video partition.

    Requirement 6.5: known-country plus unknown-country channels must equal
    resolved channels. The model enforces that at construction, so a
    miscount cannot be published.
    """
    cur.execute(
        f"""
        with {_FILTERED_OCCURRENCE_CTE},
             {_SELECTED_VIDEO_CTE},
             {_SELECTED_CHANNEL_CTE},
        referenced_channel as (
            select distinct sv.channel_id
            from filtered_occurrence f
            join selected_video sv on sv.video_id = f.video_id
            where sv.status = 'Resolved'
        )
        select
            count(*) filter (where sc.status = 'Resolved') as resolved,
            count(*) filter (
                where sc.status = 'Resolved' and sc.declared_country is not null
            ) as known_country,
            count(*) filter (
                where sc.status = 'Resolved' and sc.declared_country is null
            ) as unknown_country
        from referenced_channel r
        join selected_channel sc on sc.channel_id = r.channel_id
        """,
        _filter_params(inputs),
    )
    row = cur.fetchone()
    resolved_channels = int(str(row[0])) if row else 0
    known = int(str(row[1])) if row else 0
    unknown = int(str(row[2])) if row else 0

    return CoverageSummary(
        input_occurrence_count=occurrence_count,
        distinct_input_video_count=partition.distinct_input_video_count,
        resolved_video_count=partition.resolved_count,
        unavailable_video_count=partition.unavailable_unclassified_count,
        resolved_channel_count=resolved_channels,
        known_country_channel_count=known,
        unknown_country_channel_count=unknown,
    )


def compute_creator_aggregates(
    cur: psycopg.Cursor[tuple[object, ...]],
    inputs: AggregateInputs,
    *,
    limit: int | None = None,
) -> list[CreatorAggregate]:
    """Compute per-channel represented-video counts and dataset breakdowns.

    Requirement 5.3: a video shared across datasets counts once in the
    combined total and once in each applicable dataset breakdown, which is
    why the breakdown is computed with its own distinct count per dataset
    rather than by partitioning the combined total.
    """
    # The per-channel totals and the per-dataset breakdown are computed as
    # two ordered passes rather than one join. Joining them in SQL fans out
    # to (channels x datasets) rows before aggregating, which on a corpus of
    # ~29,000 channels exceeded the statement timeout; two grouped scans
    # stay linear and the results are stitched by key in Python.
    cur.execute(
        f"""
        with {_FILTERED_OCCURRENCE_CTE},
             {_SELECTED_VIDEO_CTE},
             {_SELECTED_CHANNEL_CTE},
        attributed as (
            select
                sv.channel_id,
                sc.display_name,
                coalesce(sc.declared_country, %(unknown)s) as country,
                f.video_id
            from filtered_occurrence f
            join selected_video sv on sv.video_id = f.video_id
            join selected_channel sc on sc.channel_id = sv.channel_id
            where sv.status = 'Resolved' and sc.status = 'Resolved'
        )
        select channel_id,
               min(display_name) as display_name,
               min(country) as country,
               count(distinct video_id) as represented_video_count
        from attributed
        group by channel_id
        order by count(distinct video_id) desc, channel_id
        """
        + (f" limit {int(limit)}" if limit is not None else ""),
        {**_filter_params(inputs), "unknown": UNKNOWN_COUNTRY},
    )

    totals = [
        (
            str(row[0]),
            str(row[1]) if row[1] is not None else None,
            str(row[2]),
            int(str(row[3])),
        )
        for row in cur.fetchall()
    ]
    if not totals:
        return []

    wanted = [channel_id for channel_id, _, _, _ in totals]
    cur.execute(
        f"""
        with {_FILTERED_OCCURRENCE_CTE},
             {_SELECTED_VIDEO_CTE}
        select sv.channel_id, f.dataset_id, count(distinct f.video_id)
        from filtered_occurrence f
        join selected_video sv on sv.video_id = f.video_id
        where sv.status = 'Resolved'
          and sv.channel_id = any(%(channels)s)
        group by sv.channel_id, f.dataset_id
        order by sv.channel_id, f.dataset_id
        """,
        {**_filter_params(inputs), "channels": wanted},
    )

    breakdown: dict[str, list[tuple[str, int]]] = {}
    for row in cur.fetchall():
        breakdown.setdefault(str(row[0]), []).append((str(row[1]), int(str(row[2]))))

    return [
        CreatorAggregate(
            channel_id=channel_id,
            display_name=display_name,
            country=country,
            represented_video_count=video_count,
            dataset_breakdown=tuple(breakdown.get(channel_id, [])),
        )
        for channel_id, display_name, country, video_count in totals
    ]


def build_aggregates(
    cur: psycopg.Cursor[tuple[object, ...]],
    inputs: AggregateInputs,
    *,
    creator_limit: int | None = None,
) -> AggregateResult:
    """Build every aggregate for one release and filter."""
    occurrence_count, distinct_videos = compute_headline_counts(cur, inputs)
    partition = compute_partition(cur, inputs)
    coverage = compute_coverage(cur, inputs, occurrence_count=occurrence_count, partition=partition)
    countries = compute_country_summaries(cur, inputs)
    creators = compute_creator_aggregates(cur, inputs, limit=creator_limit)

    result = AggregateResult(
        countries=countries,
        creators=creators,
        coverage=coverage,
        partition=partition,
        represented_video_count=distinct_videos,
        creator_count=coverage.resolved_channel_count,
    )
    _assert_country_totals_reconcile(result, occurrence_count, distinct_videos)
    return result


class AggregateReconciliationError(RuntimeError):
    """Raised when country subtotals contradict the corpus totals."""


def _assert_country_totals_reconcile(
    result: AggregateResult, occurrence_count: int, distinct_videos: int
) -> None:
    """Fail closed when per-country subtotals exceed the whole.

    Country buckets are disjoint (Requirement 5.5 assigns each video to
    exactly one), so their subtotals can never exceed the corpus totals.
    A join that fans out on duplicate occurrences silently violates this,
    which is how it was found: a country reported more source occurrences
    than the entire filtered corpus contained.
    """
    country_videos = sum(s.represented_video_count for s in result.countries)
    if country_videos > distinct_videos:
        msg = (
            f"country represented-video subtotals sum to {country_videos}, "
            f"exceeding the {distinct_videos} distinct videos in the filter"
        )
        raise AggregateReconciliationError(msg)

    country_occurrences = sum(s.source_occurrence_count for s in result.countries)
    if country_occurrences > occurrence_count:
        msg = (
            f"country source-occurrence subtotals sum to {country_occurrences}, "
            f"exceeding the {occurrence_count} occurrences in the filter"
        )
        raise AggregateReconciliationError(msg)

    country_creators = sum(s.creator_count for s in result.countries)
    if country_creators > result.creator_count:
        msg = (
            f"country creator subtotals sum to {country_creators}, exceeding "
            f"the {result.creator_count} resolved channels"
        )
        raise AggregateReconciliationError(msg)


def default_filter(
    cur: psycopg.Cursor[tuple[object, ...]],
) -> Filter:
    """Build a filter admitting every approved dataset.

    Sorted and deduplicated so the filter has one canonical form
    (Requirement 11.2).
    """
    cur.execute(
        "select distinct dataset_id, corpus_class from provenance.dataset_contract "
        "where access_status = 'Approved' order by dataset_id"
    )
    rows = cur.fetchall()
    datasets = sorted({str(row[0]) for row in rows})
    classes = sorted({CorpusClass(str(row[1])) for row in rows})
    return Filter(datasets=tuple(datasets), corpus_classes=tuple(classes))
