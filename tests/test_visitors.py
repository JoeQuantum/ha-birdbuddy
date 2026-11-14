"""Canonical visits feed coherent history, sensors and image carousel entities."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from birdbuddy.exceptions import GraphqlError
from birdbuddy.feed import Feed
from birdbuddy.visits import normalize_visit
import pytest

from custom_components.birdbuddy.device import BirdBuddyDevice
from custom_components.birdbuddy.image import (
    BirdBuddyIndexedRecentVisitorImageEntity,
    BirdBuddyRecentVisitorImageEntity,
)
from custom_components.birdbuddy.sensor import BirdBuddyRecentVisitorEntity
from custom_components.birdbuddy.visitors import RecentVisitors

STAMP = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
URL = "https://cdn.example.invalid/current.jpg?Expires=99999999999"
EXPIRED = "https://cdn.example.invalid/old.jpg?Expires=1"


def _visit(
    key="visit-1",
    *,
    minute=0,
    feeder: str | None = "f1",
    species: str | None = "Robin",
    url=URL,
    kind="FeedItemSpeciesSighting",
    media_kind="MediaImage",
    **changes,
):
    payload = {
        "id": key,
        "__typename": kind,
        "createdAt": STAMP.replace(minute=minute).isoformat(),
        "feeder": {"id": feeder} if feeder else None,
        "media": {
            "id": "media-" + key,
            "__typename": media_kind,
            "createdAt": STAMP.replace(minute=minute).isoformat(),
            "contentUrl": url,
            "thumbnailUrl": url,
        },
        "species": [{"id": species, "name": species}] if species else [],
        **changes,
    }
    return normalize_visit(payload, known_feeder_ids=("f1", "f2"))


def _manager(hass, feeder_id="f1", *, client=None, count=5, dedupe=False) -> Any:
    feeder = BirdBuddyDevice(
        {"id": feeder_id, "name": "Backyard", "__typename": "FeederForOwner"}
    )
    client = client if client is not None else MagicMock()
    client.feeders = {"f1": {}, "f2": {}}
    client.get_feed_item = AsyncMock()
    client.feed = AsyncMock()
    return RecentVisitors(feeder, client, hass, count=count, dedupe_by_species=dedupe)


def _consumers(hass, manager):
    coordinator = MagicMock()
    coordinator.visitors = {manager.feeder.id: manager}
    coordinator.feeders = {manager.feeder.id: manager.feeder}
    coordinator.add_visitor_listener.side_effect = lambda _feeder, listener: (
        manager.register_callback(listener)
    )
    sensor = BirdBuddyRecentVisitorEntity(manager.feeder, coordinator)
    image = BirdBuddyRecentVisitorImageEntity(hass, manager.feeder, coordinator)
    indexed = BirdBuddyIndexedRecentVisitorImageEntity(
        hass, manager.feeder, coordinator, 2
    )
    for consumer in (sensor, image, indexed):
        consumer.hass = hass
        cast(Any, consumer).async_write_ha_state = MagicMock()
        manager.register_callback(consumer._on_recent_visitor)
    return sensor, image, indexed


async def test_five_visits_keep_same_species_and_stable_ties(hass):
    manager = _manager(hass)
    manager.apply_visits(
        [_visit(f"visit-{number}", minute=number // 2) for number in range(8)]
    )
    assert [visit.visit_id for visit in manager.recent] == [
        "visit-7",
        "visit-6",
        "visit-5",
        "visit-4",
        "visit-3",
    ]
    assert [visit.species.name for visit in manager.recent] == ["Robin"] * 5
    manager.apply_visits([])
    assert len(manager.recent) == 5
    manager.client.feed.assert_not_awaited()


async def test_configured_count_and_species_dedupe_are_preserved(hass):
    manager = _manager(hass, count=3, dedupe=True)
    manager.apply_visits(
        [
            _visit("a", minute=1),
            _visit("b", minute=2),
            _visit("c", minute=3, species="Finch"),
            _visit("d", minute=4, species=None),
            _visit("e", minute=5, species=None),
        ]
    )
    assert [visit.visit_id for visit in manager.recent] == ["e", "d", "c"]


async def test_same_species_different_feeders_and_no_sole_feeder_guess(hass):
    client = MagicMock()
    first = _manager(hass, "f1", client=client)
    second = _manager(hass, "f2", client=client)
    visits = [
        _visit("first", feeder="f1"),
        _visit("second", feeder="f2"),
        _visit("unknown", feeder=None),
    ]
    for manager in (first, second):
        manager.apply_visits(visits)
    assert [v.visit_id for v in first.recent] == ["first"]
    assert [v.visit_id for v in second.recent] == ["second"]


async def test_recognized_unknown_and_same_species_consumers_stay_coherent(hass):
    manager = _manager(hass)
    sensor, image, second = _consumers(hass, manager)
    manager.apply_visits([_visit("recognized")])
    assert sensor.native_value == "Robin"
    assert image.image_url == sensor.entity_picture == URL
    manager.apply_visits(
        [
            _visit(
                "unknown",
                minute=1,
                species=None,
                url="https://cdn.example.invalid/unknown.jpg",
                kind="FeedItemNewPostcard",
            )
        ]
    )
    assert sensor.native_value is None
    assert (
        image.image_url
        == sensor.entity_picture
        == "https://cdn.example.invalid/unknown.jpg"
    )
    assert second.image_url == URL
    manager.apply_visits(
        [_visit("repeat", minute=2, url="https://cdn.example.invalid/repeat.jpg")]
    )
    assert sensor.native_value == "Robin"
    assert (
        image.image_url
        == sensor.entity_picture
        == "https://cdn.example.invalid/repeat.jpg"
    )
    assert image.image_last_updated == manager.recent[0].created_at
    assert image.image_last_updated is not None
    assert (
        sensor.extra_state_attributes["visitors"][0]["created_at"]
        == image.image_last_updated.isoformat()
    )


async def test_nested_collection_species_and_singular_media_seed_before_registration(
    hass,
):
    manager = _manager(hass)
    visit = _visit(
        kind="FeedItemCollectedPostcard",
        species=None,
        collection={"species": {"id": "finch", "name": "Finch"}},
    )
    manager.apply_visits([visit])
    sensor, image, _ = _consumers(hass, manager)
    assert sensor.native_value == "Finch"
    assert image.image_url == URL
    manager.client.feed.assert_not_awaited()


async def test_media_free_visit_clears_its_image_without_erasing_other_history(hass):
    manager = _manager(hass)
    sensor, image, second = _consumers(hass, manager)
    manager.apply_visits([_visit("first")])
    manager.apply_visits([_visit("newer", minute=1, species="Finch", media=None)])
    assert sensor.native_value == "Finch"
    assert sensor.entity_picture is None
    assert image.image_url is None
    assert second.image_url == URL
    assert len(manager.recent) == 2
    assert sensor.extra_state_attributes["visitors"][0]["media_url"] is None


@pytest.mark.parametrize("thumbnail", [URL, None])
async def test_video_only_visit_preserves_time_without_returning_video_as_image(
    hass, thumbnail
):
    manager = _manager(hass)
    sensor, image, _ = _consumers(hass, manager)
    raw = _visit(
        media_kind="MediaVideo", url="https://cdn.example.invalid/video.mp4"
    ).raw
    raw["media"]["thumbnailUrl"] = thumbnail
    manager.apply_visits([normalize_visit(raw)])
    assert manager.latest_media.is_video
    assert manager.recent[0].created_at == STAMP
    assert image.image_url == sensor.entity_picture == thumbnail
    assert image.image_url != "https://cdn.example.invalid/video.mp4"


async def test_missing_capture_time_stays_unknown_in_both_consumers(hass):
    manager = _manager(hass)
    sensor, image, _ = _consumers(hass, manager)
    manager.apply_visits([_visit(createdAt=None)])
    assert image.image_url == URL
    assert image.image_last_updated is None
    assert sensor.extra_state_attributes["visitors"][0]["created_at"] is None


async def test_same_id_collection_update_replaces_in_place_without_duplicate(hass):
    manager = _manager(hass)
    manager.apply_visits([_visit(kind="FeedItemNewPostcard", species=None)])
    manager.apply_visits([_visit(kind="FeedItemCollectedPostcard")])
    assert len(manager.recent) == 1
    assert manager.latest_species.name == "Robin"


async def test_explicit_reset_clears_all_consumers(hass):
    manager = _manager(hass)
    sensor, image, second = _consumers(hass, manager)
    manager.apply_visits([_visit("first"), _visit("second", minute=1)])
    manager.apply_visits([], replace=True)
    assert manager.recent == []
    assert sensor.native_value is None
    assert sensor.entity_picture is None
    assert image.image_url is None
    assert second.image_url is None


async def test_refresh_reads_only_requested_visit_and_updates_all_bound_consumers(hass):
    manager = _manager(hass)
    _, image, second = _consumers(hass, manager)
    manager.apply_visits(
        [_visit("first", url=EXPIRED), _visit("second", minute=1, url=EXPIRED)]
    )
    manager.client.get_feed_item.side_effect = lambda key: _visit(
        key, minute=1 if key == "second" else 0
    )
    with patch(
        "custom_components.birdbuddy.image._BirdBuddyVisitorImage._async_load_image_from_url",
        new=AsyncMock(
            return_value=SimpleNamespace(content=b"image", content_type="image/jpeg")
        ),
    ):
        assert await image.async_image() == b"image"
        assert await second.async_image() == b"image"
    assert [call.args for call in manager.client.get_feed_item.await_args_list] == [
        ("second",),
        ("first",),
    ]
    manager.client.feed.assert_not_awaited()
    manager.client.identify_postcard.assert_not_called()
    manager.client.collect_postcard.assert_not_called()


async def test_concurrent_same_visit_refresh_is_shared_by_account(hass):
    manager = _manager(hass)
    manager.apply_visits([_visit(url=EXPIRED)])
    started, release = asyncio.Event(), asyncio.Event()

    async def read(_key):
        started.set()
        await release.wait()
        return _visit()

    manager.client.get_feed_item.side_effect = read
    first = asyncio.create_task(manager.async_update())
    await started.wait()
    second = asyncio.create_task(manager.async_update())
    release.set()
    await asyncio.gather(first, second)
    manager.client.get_feed_item.assert_awaited_once_with("visit-1")
    assert manager.latest_media.content_url == URL


async def test_cold_read_is_one_shared_account_feed_not_one_per_feeder(hass):
    client = MagicMock()
    first = _manager(hass, client=client)
    second = _manager(hass, "f2", client=client)
    client.feed.return_value = Feed(
        {
            "edges": [
                {"node": _visit("first").raw},
                {"node": _visit("second", feeder="f2").raw},
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }
    )
    await asyncio.gather(first.async_update(), second.async_update())
    client.feed.assert_awaited_once_with(first=50)
    assert first.recent[0].visit_id == "first"
    assert second.recent[0].visit_id == "second"


@pytest.mark.parametrize("reset", [False, True])
async def test_newer_poll_or_reset_wins_inflight_detail_race(hass, reset):
    manager = _manager(hass)
    sensor, image, _ = _consumers(hass, manager)
    manager.apply_visits([_visit(url=EXPIRED)])
    started, release = asyncio.Event(), asyncio.Event()

    async def read(_key):
        started.set()
        await release.wait()
        return _visit(url="https://cdn.example.invalid/old-response.jpg")

    manager.client.get_feed_item.side_effect = read
    request = asyncio.create_task(manager.async_update())
    await started.wait()
    manager.apply_visits([] if reset else [_visit(url=URL)], replace=reset)
    release.set()
    await request
    assert (
        manager.latest_media is None
        if reset
        else manager.latest_media.content_url == URL
    )
    assert image.image_url == sensor.entity_picture == (None if reset else URL)


async def test_transient_error_preserves_identity_and_valid_media(hass):
    manager = _manager(hass)
    manager.apply_visits([_visit()])
    manager.client.get_feed_item.side_effect = TimeoutError("private signed URL")
    with pytest.raises(TimeoutError):
        await manager.async_update()
    assert manager.recent[0].visit_id == "visit-1"
    assert manager.latest_media.content_url == URL


async def test_confirmed_missing_item_keeps_identity_without_stale_asset(hass):
    manager = _manager(hass)
    manager.apply_visits([_visit()])
    manager.client.get_feed_item.side_effect = GraphqlError(
        {"extensions": {"code": "NOT_FOUND"}}
    )
    await manager.async_update()
    assert manager.recent[0].visit_id == "visit-1"
    assert manager.recent[0].created_at == STAMP
    assert manager.latest_media is None


async def test_callback_unregister_is_idempotent_and_no_network_on_registration(hass):
    manager = _manager(hass)
    listener = MagicMock()
    original_listeners = hass.bus.async_listeners()
    remove = manager.register_callback(listener)
    assert hass.bus.async_listeners() == original_listeners
    listener.assert_called_once_with(manager)
    remove()
    remove()
    manager.apply_visits([_visit()])
    listener.assert_called_once()
    manager.client.feed.assert_not_awaited()


async def test_two_accounts_with_same_feeder_and_item_id_do_not_share_refresh(hass):
    first = _manager(hass)
    second = _manager(hass)
    for manager in (first, second):
        manager.apply_visits([_visit(url=EXPIRED)])
    first.client.get_feed_item.return_value = _visit(url=URL)
    second.client.get_feed_item.return_value = _visit(
        url="https://cdn.example.invalid/other-account.jpg"
    )
    await asyncio.gather(first.async_update(), second.async_update())
    first.client.get_feed_item.assert_awaited_once_with("visit-1")
    second.client.get_feed_item.assert_awaited_once_with("visit-1")
    assert first.latest_media.content_url == URL
    assert (
        second.latest_media.content_url
        == "https://cdn.example.invalid/other-account.jpg"
    )


async def test_partial_species_metadata_keeps_unknown_state(hass):
    manager = _manager(hass)
    sensor, image, _ = _consumers(hass, manager)
    raw = _visit().raw
    raw["species"] = [{"id": "known-id", "name": None}]
    manager.apply_visits([normalize_visit(raw)])
    assert sensor.native_value is None
    assert image.image_url == URL
    assert manager.recent[0].visit_id == "visit-1"


async def test_unchanged_poll_does_not_discard_valid_inflight_refresh(hass):
    manager = _manager(hass)
    original = _visit(url=EXPIRED)
    manager.apply_visits([original])
    started, release = asyncio.Event(), asyncio.Event()

    async def read(_key):
        started.set()
        await release.wait()
        return _visit(url=URL)

    manager.client.get_feed_item.side_effect = read
    request = asyncio.create_task(manager.async_update())
    await started.wait()
    manager.apply_visits([original])
    release.set()
    await request
    assert manager.latest_media.content_url == URL


async def test_failed_refresh_cannot_log_private_urls(hass, caplog):
    manager = _manager(hass)
    secret = URL + "&Signature=private-signature"
    manager.apply_visits([_visit(url=EXPIRED)])
    manager.client.get_feed_item.side_effect = TimeoutError(secret)
    with pytest.raises(TimeoutError):
        await manager.async_update()
    assert "private-signature" not in caplog.text
