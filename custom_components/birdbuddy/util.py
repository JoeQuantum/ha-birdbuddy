"""Bird Buddy utilities"""

from birdbuddy.feed import FeedNode

from .const import LOGGER


def _media_list(item: FeedNode) -> list[dict]:
    """Return the media dicts on a feed node, normalizing the two API shapes.

    pybirdbuddy's FEED query returns media differently depending on the node
    type: `FeedItemSpeciesSighting` and `FeedItemMysteryVisitor*` expose a
    single `media` object, while `FeedItemCollectedPostcard` exposes a `medias`
    array. Earlier code only read the `medias` array, so sighting and mystery
    nodes — the *only* kind a feeder without auto-ID ever produces — never
    matched and the recent-visitor list always came back empty (#7).
    """
    if medias := item.get("medias"):
        return list(medias)
    if media := item.get("media"):
        return [media]
    return []


def _find_media(
    feeder_id: str,
    items: list[FeedNode],
    *,
    require_feeder_match: bool = True,
) -> list[FeedNode]:
    """Return feed items that carry an image from this feeder.

    A `species` is NOT required: mystery (unrecognized) visitors — the only
    kind a feeder without auto-ID produces — have media but no species, and
    must still surface as recent visitors with `species=None`. See issue #7.

    The single matched image is attached back onto the node as `media`.

    The feed has no per-item feeder field, so media is attributed to a feeder by
    matching its id inside the (signed) thumbnail URL. `require_feeder_match`
    can be turned off by callers that have already established the media belongs
    to this feeder (e.g. the account has a single feeder), since that URL
    heuristic does not hold on every account.
    """
    found: list[FeedNode] = []
    for item in items:
        if not item:
            continue
        images = [
            m
            for m in _media_list(item)
            if m and m.get("__typename") == "MediaImage"
        ]
        if not images:
            continue
        if require_feeder_match:
            mine = [m for m in images if feeder_id in m.get("thumbnailUrl", "")]
            if not mine:
                # Images present but none attributable to this feeder by URL —
                # the most likely place a still-empty recent-visitor list would
                # originate. Log it so it shows up in a debug capture.
                LOGGER.debug(
                    "Feed node %s has %d image(s) but none match feeder %s by "
                    "URL; skipping. First thumbnail: %s",
                    item.get("id"),
                    len(images),
                    feeder_id,
                    images[0].get("thumbnailUrl"),
                )
                continue
        else:
            mine = images
        found.append(item | {"media": next(iter(mine), None)})
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
        "suggestions": [
            _slim_suggestion(s) for s in sighting.get("suggestions", [])
        ],
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
            "sightings": [
                _slim_sighting_node(s) for s in report.get("sightings", [])
            ],
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
