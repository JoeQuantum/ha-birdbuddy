"""Tests for custom_components.birdbuddy.util."""

from __future__ import annotations

import json

from birdbuddy.feed import FeedNode

from custom_components.birdbuddy.util import _find_media, slim_event_payload

# HA's recorder caps event_data at 32768 bytes. We aim well under that.
RECORDER_EVENT_CAP_BYTES = 32768


def _long_url(name: str) -> str:
    # Real signed CloudFront / S3 URLs are ~600-900 chars (path + Key-Pair-Id +
    # Signature + Expires + Policy). We mimic the size.
    return (
        f"https://media.mybirdbuddy.com/sightings/{name}.jpg"
        f"?Expires=9999999999&Policy={'A' * 200}"
        f"&Signature={'B' * 300}&Key-Pair-Id={'K' * 60}"
    )


def _locale_names(base: str) -> dict[str, str]:
    return {
        loc: f"{base} ({loc})"
        for loc in (
            "en", "de", "fr", "es", "it", "nl", "pt", "sv",
            "no", "da", "fi", "pl", "cs", "ja", "zh",
        )
    }


def _fat_species(species_id: str, name: str) -> dict:
    return {
        "__typename": "SpeciesBird",
        "id": species_id,
        "name": name,
        "iconUrl": _long_url(f"species-{species_id}-icon"),
        # Bloat that should be dropped:
        "translations": _locale_names(name),
        "description": "Lorem ipsum dolor sit amet, " * 30,
        "habitat": "Lorem ipsum " * 20,
        "diet": "Lorem ipsum " * 20,
        "audioUrl": _long_url(f"species-{species_id}-audio"),
    }


def _fat_media(media_id: str) -> dict:
    return {
        "__typename": "MediaImage",
        "id": media_id,
        "thumbnailUrl": _long_url(f"thumb-{media_id}"),
        "contentUrl": _long_url(f"content-{media_id}"),
        # Bloat that should be dropped:
        "createdAt": "2026-05-30T12:00:00Z",
        "width": 1920,
        "height": 1080,
        "exif": {"camera": "BirdBuddy v2", "iso": 400, "shutter": "1/250"},
    }


def _fat_sighting_node(sighting_id: str) -> dict:
    return {
        "id": sighting_id,
        "__typename": "SightingCantDecideWhichBird",
        "matchTokens": [f"mt-{sighting_id}-{i}" for i in range(3)],
        "species": _fat_species("sp-1", "American Goldfinch"),
        "suggestions": [
            {
                "__typename": "CollectionSpecies",
                "id": f"col-{i}",
                "species": _fat_species(f"sp-{i}", f"Suggested Bird {i}"),
                "visitsAllTime": i * 3,
                "coverCollectionMedia": {"media": _fat_media(f"cover-{i}")},
            }
            for i in range(8)
        ],
    }


def _fat_postcard_sighting() -> tuple[dict, dict]:
    postcard = {
        "__typename": "FeedItemNewPostcard",
        "id": "postcard-abc-123",
        "createdAt": "2026-05-30T12:00:00Z",
        "medias": [_fat_media(f"pc-media-{i}") for i in range(6)],
        "feeder": {"id": "feeder-1", "name": "Backyard"},
    }
    sighting = {
        "feeder": {
            "id": "feeder-1",
            "name": "Backyard Feeder",
            "site": {
                "id": "site-1",
                "name": "Home",
                "owner": {"id": "user-1", "name": "Ben", "email": "x@y.z"},
                "members": [{"id": f"m-{i}"} for i in range(10)],
            },
            "settings": {k: f"value-{k}" * 5 for k in "abcdefghij"},
        },
        "medias": [_fat_media(f"sighting-media-{i}") for i in range(6)],
        "videoMedia": _fat_media("video-1") | {"__typename": "MediaVideo"},
        "sightingReport": {
            "reportToken": "header." + ("X" * 600) + ".signature",
            "sightings": [_fat_sighting_node(f"s-{i}") for i in range(3)],
        },
    }
    return postcard, sighting


def test_fat_payload_is_actually_fat() -> None:
    """Sanity check: the simulated raw payload exceeds the recorder cap.

    If this ever stops being true, our test stops proving anything."""
    postcard, sighting = _fat_postcard_sighting()
    raw_size = len(json.dumps({"postcard": postcard, "sighting": sighting}))
    assert raw_size > RECORDER_EVENT_CAP_BYTES, (
        f"fat fixture is only {raw_size} bytes; make it fatter"
    )


def test_slim_payload_under_recorder_cap() -> None:
    postcard, sighting = _fat_postcard_sighting()
    slim = slim_event_payload(postcard, sighting)
    slim_size = len(json.dumps(slim))
    assert slim_size < RECORDER_EVENT_CAP_BYTES, (
        f"slim payload is {slim_size} bytes, over the {RECORDER_EVENT_CAP_BYTES} cap"
    )


def test_slim_payload_keeps_service_handler_fields() -> None:
    """The collect_postcard service handler reconstructs PostcardSighting from
    data["sighting"] and uses .report (reportToken + sightings[].id/__typename/
    matchTokens/species/suggestions), .feeder["id"], and .video_media[*].id."""
    postcard, sighting = _fat_postcard_sighting()
    slim = slim_event_payload(postcard, sighting)

    assert slim["postcard"]["id"] == "postcard-abc-123"
    assert slim["sighting"]["feeder"]["id"] == "feeder-1"
    assert slim["sighting"]["videoMedia"]["id"] == "video-1"

    report = slim["sighting"]["sightingReport"]
    assert report["reportToken"].startswith("header.")
    assert len(report["sightings"]) == 3
    s0 = report["sightings"][0]
    assert s0["id"] == "s-0"
    assert s0["__typename"] == "SightingCantDecideWhichBird"
    assert s0["matchTokens"] == ["mt-s-0-0", "mt-s-0-1", "mt-s-0-2"]
    assert s0["species"]["id"] == "sp-1"
    assert len(s0["suggestions"]) == 8
    # suggestions retain id + species.id (needed for picking BEST_GUESS)
    assert s0["suggestions"][0]["species"]["id"] == "sp-0"


def test_slim_payload_drops_known_bloat() -> None:
    postcard, sighting = _fat_postcard_sighting()
    slim = slim_event_payload(postcard, sighting)

    # postcard.medias (raw FeedNode media list) should be dropped
    assert "medias" not in slim["postcard"]
    # full medias[] dropped; coverMedia keeps a single thumbnail for UX
    assert "medias" not in slim["sighting"]
    assert slim["sighting"]["coverMedia"]["thumbnailUrl"].startswith("https://")
    # feeder deep context dropped
    assert "site" not in slim["sighting"]["feeder"]
    assert "settings" not in slim["sighting"]["feeder"]
    # species locale translations / long-form text dropped
    sp = slim["sighting"]["sightingReport"]["sightings"][0]["species"]
    assert "translations" not in sp
    assert "description" not in sp
    assert "habitat" not in sp


def test_recognized_species_keeps_iconurl_suggestions_drop_it() -> None:
    """Species iconUrl is the dominant size cost in suggestions (~600B each, 5-10
    per sighting). We keep it on the recognized species (richer notifications)
    and drop it on suggestions."""
    postcard, sighting = _fat_postcard_sighting()
    slim = slim_event_payload(postcard, sighting)
    s0 = slim["sighting"]["sightingReport"]["sightings"][0]
    assert "iconUrl" in s0["species"]
    for suggestion in s0["suggestions"]:
        assert "iconUrl" not in suggestion["species"], suggestion


def test_slim_handles_empty_inputs() -> None:
    slim = slim_event_payload({}, {})
    assert slim == {
        "postcard": {"id": None, "__typename": None, "createdAt": None},
        "sighting": {
            "feeder": {"id": None, "name": None},
            "sightingReport": {"reportToken": None, "sightings": []},
        },
    }


def test_slim_handles_missing_medias_and_video() -> None:
    postcard, sighting = _fat_postcard_sighting()
    sighting.pop("medias")
    sighting.pop("videoMedia")
    slim = slim_event_payload(postcard, sighting)
    assert "coverMedia" not in slim["sighting"]
    assert "videoMedia" not in slim["sighting"]


# --- _find_media: media attribution across the two real feed-node shapes ---

FEEDER = "feeder-xyz"


def _image(media_id: str, *, feeder: str = FEEDER, typename: str = "MediaImage") -> dict:
    return {
        "__typename": typename,
        "id": media_id,
        "contentUrl": f"https://media.example.com/{feeder}/{media_id}.jpg",
        "thumbnailUrl": f"https://media.example.com/{feeder}/{media_id}-thumb.jpg",
        "createdAt": "2026-06-23T12:00:00Z",
    }


def test_find_media_singular_media_shape() -> None:
    """Sighting / mystery nodes carry a *singular* `media` object. This is the
    real API shape (#7) and earlier code missed it entirely."""
    node = FeedNode(
        {
            "__typename": "FeedItemMysteryVisitorNotRecognized",
            "id": "n1",
            "media": _image("m1"),
        }
    )
    out = _find_media(FEEDER, [node])
    assert len(out) == 1
    assert out[0]["media"]["id"] == "m1"


def test_find_media_plural_medias_shape() -> None:
    """Collected-postcard nodes carry a `medias` array; that shape must still
    work (the first matching image is attached)."""
    node = FeedNode(
        {
            "__typename": "FeedItemCollectedPostcard",
            "id": "n2",
            "medias": [_image("a"), _image("b")],
        }
    )
    out = _find_media(FEEDER, [node])
    assert len(out) == 1
    assert out[0]["media"]["id"] == "a"


def test_find_media_filters_other_feeders() -> None:
    """Media whose URL belongs to a different feeder must be dropped — the feed
    is account-wide and has no per-item feeder field."""
    node = FeedNode(
        {
            "__typename": "FeedItemMysteryVisitorNotRecognized",
            "id": "n3",
            "media": _image("m1", feeder="some-other-feeder"),
        }
    )
    assert _find_media(FEEDER, [node]) == []


def test_find_media_without_feeder_match_accepts_any_image() -> None:
    """With `require_feeder_match=False`, an image whose URL doesn't carry this
    feeder's id is still accepted (used for the single-feeder postcard case)."""
    node = FeedNode(
        {
            "__typename": "FeedItemNewPostcard",
            "id": "n5",
            "medias": [_image("m1", feeder="some-other-feeder")],
        }
    )
    assert _find_media(FEEDER, [node]) == []
    out = _find_media(FEEDER, [node], require_feeder_match=False)
    assert len(out) == 1
    assert out[0]["media"]["id"] == "m1"


def test_find_media_ignores_video_only_nodes() -> None:
    """A node with only a video (no image) yields nothing — the surface shows
    a still image."""
    node = FeedNode(
        {
            "__typename": "FeedItemSpeciesSighting",
            "id": "n4",
            "media": _image("v1", typename="MediaVideo"),
        }
    )
    assert _find_media(FEEDER, [node]) == []
