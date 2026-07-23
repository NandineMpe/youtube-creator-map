"""Deterministic creator sorting and cursor pagination.

Requirement 10.5 partitions creator rows by a configured page size, a
versioned sort order, and a deterministic tie-breaker. Requirement 10.6
requires traversing every page of one country to present each approved
creator exactly once, without omission; 10.7 requires the same cursor and
sort to return the same rows.

Those three together rule out offset pagination. An offset is positionally
defined, so it cannot promise exactly-once traversal, and it re-scans the
prefix on every page. A keyset cursor encoding the last row's sort key is
stable, exact, and O(page).

The tie-breaker matters more than it looks: sorting creators by video count
alone is not a total order, because thousands of creators share a count.
Appending the public channel key makes the order total, so a cursor always
identifies one position rather than a band of ties.

Requirement refs: 5.3-5.5, 10.5-10.8, 14.5
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from enum import StrEnum, unique

#: Versioned so a cursor minted under one ordering cannot be replayed
#: against another and silently skip or repeat rows.
CURSOR_VERSION = "1"


class InvalidCursor(ValueError):
    """Raised when a cursor is malformed, or minted for another sort order."""


@unique
class CreatorSortOrder(StrEnum):
    """The supported creator orderings.

    Each is completed by the public channel key, so every ordering is total.
    """

    VIDEO_COUNT_DESC = "representedVideoCountDesc"
    DISPLAY_NAME_ASC = "displayNameAsc"


@dataclass(frozen=True, slots=True)
class CreatorRow:
    """One disclosure-approved creator row, ready to paginate."""

    public_channel_key: str
    display_name: str
    country: str
    represented_video_count: int
    dataset_breakdown: tuple[tuple[str, int], ...]
    last_observed_at: str


def sort_key(row: CreatorRow, order: CreatorSortOrder) -> tuple[object, ...]:
    """Return the total sort key for a row under one ordering."""
    if order is CreatorSortOrder.VIDEO_COUNT_DESC:
        # Negated so ascending tuple comparison yields descending counts,
        # keeping one comparison direction throughout.
        return (-row.represented_video_count, row.public_channel_key)
    return (row.display_name.casefold(), row.public_channel_key)


def sort_rows(rows: list[CreatorRow], order: CreatorSortOrder) -> list[CreatorRow]:
    """Sort rows into the total order for one sort option."""
    return sorted(rows, key=lambda row: sort_key(row, order))


def encode_cursor(row: CreatorRow, order: CreatorSortOrder) -> str:
    """Encode the position after `row` as an opaque cursor.

    The payload carries only the sort key and the ordering it was minted
    for. It deliberately does not carry the raw channel identifier or any
    creator field beyond the public key, so a cursor appearing in a URL
    discloses nothing the page itself does not (Requirement 7.3).
    """
    payload = {
        "v": CURSOR_VERSION,
        "o": order.value,
        "k": row.public_channel_key,
        "c": row.represented_video_count,
        "n": row.display_name.casefold(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, order: CreatorSortOrder) -> tuple[object, ...]:
    """Decode a cursor into the sort key it points just past.

    Fails closed on anything malformed or minted for a different ordering:
    silently accepting a mismatched cursor would skip or repeat rows, which
    Requirement 10.6 forbids.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, TypeError) as exc:
        msg = "cursor is not decodable"
        raise InvalidCursor(msg) from exc

    if not isinstance(payload, dict):
        msg = "cursor payload is not an object"
        raise InvalidCursor(msg)
    if payload.get("v") != CURSOR_VERSION:
        msg = f"cursor version {payload.get('v')!r} is not supported"
        raise InvalidCursor(msg)
    if payload.get("o") != order.value:
        msg = "cursor was minted for a different sort order"
        raise InvalidCursor(msg)

    key = payload.get("k")
    if not isinstance(key, str) or not key:
        msg = "cursor is missing its position key"
        raise InvalidCursor(msg)

    if order is CreatorSortOrder.VIDEO_COUNT_DESC:
        count = payload.get("c")
        if not isinstance(count, int):
            msg = "cursor is missing its count component"
            raise InvalidCursor(msg)
        return (-count, key)

    name = payload.get("n")
    if not isinstance(name, str):
        msg = "cursor is missing its name component"
        raise InvalidCursor(msg)
    return (name, key)


@dataclass(frozen=True, slots=True)
class Page:
    """One page of creator rows."""

    rows: tuple[CreatorRow, ...]
    next_cursor: str | None
    page_size: int
    total_rows: int
    sort_order: CreatorSortOrder

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


def paginate(
    rows: list[CreatorRow],
    *,
    order: CreatorSortOrder,
    page_size: int,
    cursor: str | None = None,
) -> Page:
    """Return the page beginning after `cursor`.

    Rows are sorted into the total order, the cursor locates a position in
    that order, and the page is the next `page_size` rows. Because the order
    is total and the cursor is a key rather than an offset, traversal visits
    every row exactly once even though the underlying set is re-sorted on
    each call.
    """
    if page_size < 1:
        msg = f"page size must be >= 1; got {page_size}"
        raise ValueError(msg)

    ordered = sort_rows(rows, order)

    start = 0
    if cursor is not None:
        position = decode_cursor(cursor, order)
        # First row strictly after the cursor position. Bisecting on the
        # key rather than searching for the row itself means a cursor still
        # works when the row it named has since been suppressed.
        start = _first_index_after(ordered, position, order)

    window = ordered[start : start + page_size]
    next_cursor = (
        encode_cursor(window[-1], order) if window and start + page_size < len(ordered) else None
    )

    return Page(
        rows=tuple(window),
        next_cursor=next_cursor,
        page_size=page_size,
        total_rows=len(ordered),
        sort_order=order,
    )


def _first_index_after(
    ordered: list[CreatorRow], position: tuple[object, ...], order: CreatorSortOrder
) -> int:
    """Index of the first row sorting strictly after `position`."""
    low, high = 0, len(ordered)
    while low < high:
        mid = (low + high) // 2
        if _key_lte(sort_key(ordered[mid], order), position):
            low = mid + 1
        else:
            high = mid
    return low


def _key_lte(left: tuple[object, ...], right: tuple[object, ...]) -> bool:
    """Compare two heterogeneous sort keys element-wise.

    The keys mix ints and strings by position but never within a position,
    so comparison is well defined; this helper exists to satisfy the type
    checker without weakening the tuple types.
    """
    for a, b in zip(left, right, strict=True):
        if a == b:
            continue
        if isinstance(a, int) and isinstance(b, int):
            return a < b
        return str(a) < str(b)
    return True


def traverse_all(
    rows: list[CreatorRow], *, order: CreatorSortOrder, page_size: int
) -> list[CreatorRow]:
    """Walk every page, returning the rows in traversal sequence.

    Used by tests and by artifact generation to assert exactly-once
    traversal (Requirement 10.6) rather than assume it.
    """
    collected: list[CreatorRow] = []
    cursor: str | None = None
    # Bound the loop: a cursor bug that failed to advance would otherwise
    # spin forever rather than fail visibly.
    max_pages = len(rows) + 2

    for _ in range(max_pages):
        page = paginate(rows, order=order, page_size=page_size, cursor=cursor)
        collected.extend(page.rows)
        if page.next_cursor is None:
            return collected
        cursor = page.next_cursor

    msg = "pagination did not terminate; the cursor is not advancing"
    raise RuntimeError(msg)
