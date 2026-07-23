"""Minimal-field YouTube Data API metadata client.

This is the only component that contacts the metadata API, and the only
source of Declared_Country. It requests strictly the fields required for
approved display, attribution, country, status, and provenance
(Requirement 4.18), and never retrieves media, transcripts, thumbnails, or
contact information (Requirement 15.12).

Outbound access is restricted to the approved endpoint (Requirement 15.10);
any other destination is refused before a request is made.

Requirement refs: 3.5-3.9, 4.18, 15.10-15.12
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Final

from creator_map_schemas import (
    ChannelResolution,
    ChannelResolutionStatus,
    ErrorClass,
    VideoResolution,
    VideoResolutionStatus,
)

from creator_map_pipeline.enrichment.resolver import (
    ChannelObservationResult,
    ResolverError,
    VideoObservationResult,
)

#: The single approved metadata endpoint (Requirement 15.10).
APPROVED_API_HOST: Final = "www.googleapis.com"
_API_BASE: Final = f"https://{APPROVED_API_HOST}/youtube/v3"

#: The API accepts at most 50 IDs per list call (Requirement 4.2).
MAX_BATCH: Final = 50

#: `videos.list` and `channels.list` each cost one quota unit per call.
QUOTA_UNITS_PER_CALL: Final = 1

#: Only these parts are requested. `snippet` is the minimum that carries
#: channelId (videos) and country (channels); no other part is fetched.
_VIDEO_PARTS: Final = "snippet"
_CHANNEL_PARTS: Final = "snippet"

#: Supported ISO 3166 alpha-2 shape. The API occasionally returns values
#: outside this shape; Requirement 3.8 maps those to Unknown rather than
#: coercing them to a nearby country.
_COUNTRY_LENGTH: Final = 2


class ApiError(ResolverError):
    """An API failure carrying its classified error class."""

    def __init__(self, error_class: ErrorClass, detail: str) -> None:
        super().__init__(f"{error_class.value}: {detail}")
        self.error_class = error_class


def classify_http_status(status: int) -> ErrorClass:
    """Map an HTTP status to a policy error class.

    The distinction that matters is retryable versus terminal versus halt:
    401/403 mean the credential or policy is wrong and retrying would only
    burn quota against a request that cannot succeed (Requirement 4.11).
    """
    if status == 429:
        return ErrorClass.RATE_LIMITED
    if status in {401, 403}:
        return ErrorClass.INVALID_CREDENTIAL
    if status == 400:
        return ErrorClass.INVALID_REQUEST
    if status == 404:
        return ErrorClass.NOT_FOUND
    if 500 <= status < 600:
        return ErrorClass.SERVER
    return ErrorClass.MALFORMED_RESPONSE


def response_digest(payload: object) -> str:
    """Digest a response body for provenance and tie-breaking.

    Serialized with sorted keys so the digest depends on content rather than
    key ordering, which keeps Requirement 3.11's tie-breaker stable.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def normalize_declared_country(raw: object) -> str | None:
    """Normalize the channel country field, or return None.

    Requirement 3.8 and Invariant 6: only this field may supply a country,
    and an absent or unsupported value becomes Unknown rather than a guess.
    """
    if not isinstance(raw, str):
        return None
    candidate = raw.strip().upper()
    if len(candidate) != _COUNTRY_LENGTH or not candidate.isalpha():
        return None
    return candidate


class YouTubeMetadataResolver:
    """Resolves videos and channels through the approved metadata API."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 30.0,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        if not api_key:
            msg = "an API key is required"
            raise ValueError(msg)
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._opener = opener or urllib.request.build_opener()

    # -- request plumbing --------------------------------------------------

    def _get(self, path: str, params: dict[str, str]) -> dict[str, object]:
        """Issue one GET against the approved endpoint."""
        query = urllib.parse.urlencode({**params, "key": self._api_key})
        url = f"{_API_BASE}/{path}?{query}"

        # Requirement 15.10/15.11: refuse any destination outside the
        # allowlist before the request leaves the process.
        host = urllib.parse.urlsplit(url).hostname
        if host != APPROVED_API_HOST:
            msg = f"egress to non-approved host refused: {host}"
            raise ApiError(ErrorClass.POLICY_BLOCKED, msg)

        # S310 flags urllib with a non-literal URL because a scheme like
        # file:// or a hostile host could be injected. Neither applies: the
        # scheme is fixed by _API_BASE and the host was just checked against
        # the single-entry allowlist above, which is the control
        # Requirement 15.10 asks for.
        request = urllib.request.Request(url, method="GET")  # noqa: S310
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            # The URL carries the API key, so it must never reach the error
            # message (Requirement 15.3).
            raise ApiError(classify_http_status(exc.code), f"HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise ApiError(ErrorClass.NETWORK, type(exc).__name__) from None
        except TimeoutError:
            raise ApiError(ErrorClass.TIMEOUT, "request timed out") from None

        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            raise ApiError(ErrorClass.MALFORMED_RESPONSE, "body is not JSON") from None

        if not isinstance(parsed, dict):
            raise ApiError(ErrorClass.MALFORMED_RESPONSE, "body is not an object")
        return parsed

    # -- videos ------------------------------------------------------------

    def resolve_videos(
        self, video_ids: tuple[str, ...], *, observed_at: datetime
    ) -> VideoObservationResult:
        """Resolve videos to channel attribution.

        An ID the API omits becomes Unavailable_Unclassified: the API does
        not distinguish deleted from private from never-existed, and
        Requirement 3.6 forbids inventing that distinction.
        """
        if not video_ids:
            return VideoObservationResult(observations=(), quota_units=0)
        if len(video_ids) > MAX_BATCH:
            msg = f"batch of {len(video_ids)} exceeds the API maximum of {MAX_BATCH}"
            raise ValueError(msg)

        payload = self._get("videos", {"part": _VIDEO_PARTS, "id": ",".join(video_ids)})
        digest = response_digest(payload)

        returned: dict[str, str] = {}
        items = payload.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                video_id = item.get("id")
                snippet = item.get("snippet")
                if not isinstance(video_id, str) or not isinstance(snippet, dict):
                    continue
                channel_id = snippet.get("channelId")
                if isinstance(channel_id, str) and channel_id:
                    returned[video_id] = channel_id

        observations = tuple(
            VideoResolution(
                video_id=video_id,
                status=VideoResolutionStatus.RESOLVED,
                channel_id=returned[video_id],
                observed_at=observed_at,
                response_digest=digest,
            )
            if video_id in returned
            else VideoResolution(
                video_id=video_id,
                status=VideoResolutionStatus.UNAVAILABLE_UNCLASSIFIED,
                observed_at=observed_at,
                response_digest=digest,
            )
            for video_id in video_ids
        )

        return VideoObservationResult(observations=observations, quota_units=QUOTA_UNITS_PER_CALL)

    # -- channels ----------------------------------------------------------

    def resolve_channels(
        self, channel_ids: tuple[str, ...], *, observed_at: datetime
    ) -> ChannelObservationResult:
        """Resolve channels to display name and Declared_Country."""
        if not channel_ids:
            return ChannelObservationResult(observations=(), quota_units=0)
        if len(channel_ids) > MAX_BATCH:
            msg = f"batch of {len(channel_ids)} exceeds the API maximum of {MAX_BATCH}"
            raise ValueError(msg)

        payload = self._get("channels", {"part": _CHANNEL_PARTS, "id": ",".join(channel_ids)})
        digest = response_digest(payload)

        returned: dict[str, tuple[str | None, str | None]] = {}
        items = payload.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                channel_id = item.get("id")
                snippet = item.get("snippet")
                if not isinstance(channel_id, str) or not isinstance(snippet, dict):
                    continue
                title = snippet.get("title")
                returned[channel_id] = (
                    title if isinstance(title, str) and title else None,
                    normalize_declared_country(snippet.get("country")),
                )

        observations: list[ChannelResolution] = []
        for channel_id in channel_ids:
            if channel_id in returned:
                display_name, country = returned[channel_id]
                observations.append(
                    ChannelResolution(
                        channel_id=channel_id,
                        status=ChannelResolutionStatus.RESOLVED,
                        display_name=display_name,
                        declared_country=country,
                        observed_at=observed_at,
                        response_digest=digest,
                    )
                )
            else:
                observations.append(
                    ChannelResolution(
                        channel_id=channel_id,
                        status=ChannelResolutionStatus.UNAVAILABLE_UNCLASSIFIED,
                        observed_at=observed_at,
                        response_digest=digest,
                    )
                )

        return ChannelObservationResult(
            observations=tuple(observations), quota_units=QUOTA_UNITS_PER_CALL
        )
