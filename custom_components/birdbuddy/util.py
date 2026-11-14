"""Bird Buddy utilities"""

from collections.abc import Mapping
from urllib.parse import urlsplit

from birdbuddy.feed import FeedNode
from birdbuddy.media import Media, is_media_expired
from birdbuddy.visits import normalize_visit


def media_preview_url(media: Media | None) -> str | None:
    """Return a current still-image preview, never video or unknown content."""
    if media is None:
        return None
    if isinstance(media, Mapping):
        kind = media.get("__typename")
        if kind not in {"MediaImage", "MediaVideo"}:
            return None
        candidates = [media.get("thumbnailUrl")]
        if kind == "MediaImage":
            candidates.insert(0, media.get("contentUrl"))
    else:
        candidates = [
            getattr(media, "content_url", None),
            getattr(media, "thumbnail_url", None),
        ]
    for url in candidates:
        if not isinstance(url, str) or not url:
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"https", "http"} or not parsed.netloc:
                continue
            if is_media_expired(url):
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        return url
    return None


def _media_list(item: FeedNode) -> list[dict]:
    """Expose the canonical singular/plural/video normalization to legacy callers."""
    return [media.to_dict() for media in normalize_visit(item).medias]


def _find_media(
    feeder_id: str,
    items: list[FeedNode],
    *,
    require_feeder_match: bool = True,
) -> list[FeedNode]:
    """Return attributed image previews without URL-substring or sole-feeder guesses.

    The former opt-out flag remains accepted for call compatibility but cannot
    override missing or conflicting feeder identity. New consumers use Visit
    directly so video-only and media-free visits remain part of their history.
    """
    del require_feeder_match
    found = []
    for item in items:
        visit = normalize_visit(item, known_feeder_ids=(feeder_id,))
        if visit.feeder_id == feeder_id and (image := visit.representative_image):
            found.append(FeedNode(visit.raw | {"media": image.to_dict()}))
    return found


_SPECIES_KEYS_FULL = ("id", "__typename", "name", "iconUrl")
_SPECIES_KEYS_MINIMAL = ("id", "__typename", "name")


def _slim_species(species: dict | None, *, minimal: bool = False) -> dict:
    if not species:
        return {}
    keys = _SPECIES_KEYS_MINIMAL if minimal else _SPECIES_KEYS_FULL
    return {k: species[k] for k in keys if k in species}


def _slim_suggestion(suggestion: dict | None) -> dict:
    if not suggestion:
        return {}
    return {
        "id": suggestion.get("id"),
        "__typename": suggestion.get("__typename"),
        # iconUrl dropped here: each signed CDN URL is ~600-900 bytes, and a
        # postcard can have 5-10 suggestions per sighting, blowing the recorder
        # cap. Use the recognized species' iconUrl, or coverMedia.thumbnailUrl.
        "species": _slim_species(suggestion.get("species"), minimal=True),
    }


def _slim_sighting_node(sighting: dict) -> dict:
    return {
        "id": sighting.get("id"),
        "__typename": sighting.get("__typename"),
        "matchTokens": sighting.get("matchTokens", []),
        "species": _slim_species(sighting.get("species")),
        "suggestions": [_slim_suggestion(s) for s in sighting.get("suggestions", [])],
    }


def slim_event_payload(postcard_data: dict, sighting_data: dict) -> dict:
    """Build a recorder-safe event payload from raw postcard + sighting dicts.

    HA's recorder caps event data at 32768 bytes. Raw GraphQL responses for a
    postcard sighting routinely exceed that because of nested media URL lists,
    deep feeder context, locale-translated species text, and the full
    suggestions tree. This drops everything not needed by either:
      a) the `birdbuddy.collect_postcard` service handler, or
      b) common automation use-cases (filter by feeder, show the bird image).
    """
    feeder = sighting_data.get("feeder") or {}
    report = sighting_data.get("sightingReport") or {}
    slim_sighting: dict = {
        "feeder": {
            "id": feeder.get("id"),
            "name": feeder.get("name"),
        },
        "sightingReport": {
            "reportToken": report.get("reportToken"),
            "sightings": [_slim_sighting_node(s) for s in report.get("sightings", [])],
        },
    }
    if medias := sighting_data.get("medias"):
        cover = medias[0]
        slim_sighting["coverMedia"] = {
            "id": cover.get("id"),
            "__typename": cover.get("__typename"),
            "thumbnailUrl": cover.get("thumbnailUrl"),
            "contentUrl": cover.get("contentUrl"),
        }
    if video := sighting_data.get("videoMedia"):
        slim_sighting["videoMedia"] = {
            "id": video.get("id"),
            "__typename": video.get("__typename"),
        }
    slim_postcard = {
        "id": postcard_data.get("id"),
        "__typename": postcard_data.get("__typename"),
        "createdAt": postcard_data.get("createdAt"),
    }
    return {"postcard": slim_postcard, "sighting": slim_sighting}
