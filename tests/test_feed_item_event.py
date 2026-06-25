"""Tests for the additive EVENT_NEW_FEED_ITEM coordinator event."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from birdbuddy.feed import FeedNode

from custom_components.birdbuddy.const import EVENT_NEW_FEED_ITEM
from custom_components.birdbuddy.coordinator import (
    _SEEN_FEED_ITEM_CAP,
    BirdBuddyDataUpdateCoordinator,
)

SLIM_KEYS = {
    "item_id",
    "type",
    "created_at",
    "feeder_id",
    "media_url",
    "thumbnail_url",
}


def _make_coordinator(feeders=("f1",)) -> BirdBuddyDataUpdateCoordinator:
    from collections import deque

    c = object.__new__(BirdBuddyDataUpdateCoordinator)
    c.client = MagicMock()
    c.client.feeders = {fid: object() for fid in feeders}
    c.client._make_request = AsyncMock(return_value={})
    c.hass = MagicMock()
    c._seen_feed_ids = set()
    c._seen_feed_order = deque(maxlen=_SEEN_FEED_ITEM_CAP)
    return c


def _sighting_node(node_id: str, *, feeder: str = "f1") -> FeedNode:
    return FeedNode(
        {
            "__typename": "FeedItemSpeciesSighting",
            "id": node_id,
            "createdAt": "2026-06-25T12:00:00.000+0000",
            "media": {
                "__typename": "MediaImage",
                "id": f"m-{node_id}",
                "contentUrl": f"https://cdn.example.com/{feeder}/{node_id}.jpg",
                "thumbnailUrl": f"https://cdn.example.com/{feeder}/{node_id}-t.jpg",
            },
        }
    )


def _postcard_node(node_id: str) -> FeedNode:
    # Raw postcard as it appears in the *typed* feed: no media here.
    return FeedNode(
        {
            "__typename": "FeedItemNewPostcard",
            "id": node_id,
            "createdAt": "2026-06-25T12:01:00.000+0000",
        }
    )


def _feed(nodes: list[FeedNode]) -> MagicMock:
    feed = MagicMock()
    feed.filter.return_value = nodes
    return feed


def _postcard_media_result(node_id: str, *, feeder: str = "f1") -> dict:
    return {
        "me": {
            "feed": {
                "edges": [
                    {
                        "node": {
                            "__typename": "FeedItemNewPostcard",
                            "id": node_id,
                            "medias": [
                                {
                                    "__typename": "MediaImage",
                                    "id": f"pm-{node_id}",
                                    "contentUrl": f"https://cdn.example.com/{feeder}/{node_id}.jpg",
                                    "thumbnailUrl": f"https://cdn.example.com/{feeder}/{node_id}-t.jpg",
                                }
                            ],
                        }
                    }
                ]
            }
        }
    }


def _fired(coordinator) -> list[dict]:
    return [
        call.kwargs["event_data"]
        for call in coordinator.hass.bus.fire.call_args_list
        if call.kwargs.get("event_type") == EVENT_NEW_FEED_ITEM
    ]


def test_fires_once_per_new_item() -> None:
    c = _make_coordinator()
    c.client.feed = AsyncMock(return_value=_feed([_sighting_node("s1"), _sighting_node("s2")]))

    asyncio.run(c._fire_new_feed_item_events(seed_only=False))

    fired = _fired(c)
    assert {p["item_id"] for p in fired} == {"s1", "s2"}
    assert all(p["type"] == "FeedItemSpeciesSighting" for p in fired)
    assert all(p["feeder_id"] == "f1" for p in fired)
    assert all(p["media_url"].endswith(".jpg") for p in fired)


def test_does_not_refire_on_repeat() -> None:
    c = _make_coordinator()
    c.client.feed = AsyncMock(return_value=_feed([_sighting_node("s1")]))

    asyncio.run(c._fire_new_feed_item_events(seed_only=False))
    asyncio.run(c._fire_new_feed_item_events(seed_only=False))

    assert len(_fired(c)) == 1


def test_seed_only_marks_without_firing() -> None:
    c = _make_coordinator()
    c.client.feed = AsyncMock(return_value=_feed([_sighting_node("s1"), _sighting_node("s2")]))

    asyncio.run(c._fire_new_feed_item_events(seed_only=True))
    assert _fired(c) == []
    assert c._seen_feed_ids == {"s1", "s2"}

    # A subsequent normal poll of the same items fires nothing (already seeded).
    asyncio.run(c._fire_new_feed_item_events(seed_only=False))
    assert _fired(c) == []


def test_unidentified_postcard_carries_image() -> None:
    c = _make_coordinator()
    c.client.feed = AsyncMock(return_value=_feed([_postcard_node("p1")]))
    c.client._make_request = AsyncMock(return_value=_postcard_media_result("p1"))

    asyncio.run(c._fire_new_feed_item_events(seed_only=False))

    fired = _fired(c)
    assert len(fired) == 1
    assert fired[0]["type"] == "FeedItemNewPostcard"
    assert fired[0]["media_url"] == "https://cdn.example.com/f1/p1.jpg"
    assert fired[0]["feeder_id"] == "f1"


def test_payload_is_slim() -> None:
    c = _make_coordinator()
    c.client.feed = AsyncMock(return_value=_feed([_sighting_node("s1")]))

    asyncio.run(c._fire_new_feed_item_events(seed_only=False))

    payload = _fired(c)[0]
    assert set(payload) == SLIM_KEYS


def test_dedup_set_respects_cap() -> None:
    c = _make_coordinator()
    for i in range(_SEEN_FEED_ITEM_CAP + 100):
        c._mark_feed_item_seen(f"id-{i}")

    assert len(c._seen_feed_ids) == _SEEN_FEED_ITEM_CAP
    # Oldest evicted, newest retained.
    assert "id-0" not in c._seen_feed_ids
    assert f"id-{_SEEN_FEED_ITEM_CAP + 99}" in c._seen_feed_ids
