"""Exercise asynchronous image requests with an offline HTTP boundary."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from birdbuddy.media import Media
import pytest

from custom_components.birdbuddy import image as images
from custom_components.birdbuddy.device import BirdBuddyDevice
from custom_components.birdbuddy.image import (
    BirdBuddyIndexedRecentVisitorImageEntity,
    BirdBuddyRecentVisitorImageEntity,
)
from custom_components.birdbuddy.visitors import RecentVisitor, RecentVisitors

STAMP = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)
URL = "https://cdn.example.invalid/image.jpg?Expires=9999999999&Signature=private"
NEW_URL = "https://cdn.example.invalid/new.jpg?Expires=99999999999&Signature=private"


def media(media_id="m1", url=URL, **extra):
    return Media(
        {
            "id": media_id,
            "__typename": "MediaImage",
            "contentUrl": url,
            "thumbnailUrl": url,
            "createdAt": STAMP.isoformat(),
            **extra,
        }
    )


class Reply:
    """An async response double; retrieval still executes the full image method."""

    def __init__(
        self,
        content=b"image",
        *,
        status=200,
        content_type="text/plain",
        started=None,
        release=None,
    ):
        self.content = content
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.started = started
        self.release = release
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(real_url=URL),
                history=(),
                status=self.status,
                message=f"Rejected {URL}",
            )

    async def read(self):
        if self.started:
            self.started.set()
        if self.release:
            await self.release.wait()
        return self.content


def entity(hass, index=1):
    feeder = BirdBuddyDevice(
        {"id": "f1", "name": "Backyard", "__typename": "FeederForOwner"}
    )
    coordinator = MagicMock()
    coordinator.feeders = {"f1": feeder}
    visitors = MagicMock(spec=RecentVisitors)
    visitors.latest_media = None
    visitors.recent = []
    visitors.async_update = AsyncMock()
    coordinator.visitors = {"f1": visitors}
    if index == 1:
        result = BirdBuddyRecentVisitorImageEntity(hass, feeder, coordinator)
    else:
        result = BirdBuddyIndexedRecentVisitorImageEntity(
            hass, feeder, coordinator, index
        )
    result.hass = hass
    cast(Any, result).async_write_ha_state = MagicMock()
    return result, coordinator, visitors


def show(image, visitors, item):
    visitors.latest_media = item
    visitors.recent = [RecentVisitor(item, None, STAMP)] * 5 if item is not None else []
    image._on_recent_visitor(visitors)


@pytest.mark.parametrize("index", [1, 2, 3, 4, 5])
async def test_cleared_slot_never_serves_cached_bytes(hass, index):
    image, _, visitors = entity(hass, index)
    show(image, visitors, media())
    session = MagicMock()
    session.get.return_value = Reply(b"old")
    with patch.object(images, "async_get_clientsession", return_value=session):
        assert await image.async_image() == b"old"
        show(image, visitors, None)
        assert await image.async_image() is None
    assert not image.available
    assert image._cached_image is None
    assert image._attr_image_last_updated is None
    session.get.assert_called_once_with(URL)


@pytest.mark.parametrize(("media_id", "url"), [("m2", URL), ("m1", NEW_URL)])
async def test_id_or_url_rotation_invalidates_bytes(hass, media_id, url):
    image, _, visitors = entity(hass)
    show(image, visitors, media())
    session = MagicMock()
    session.get.side_effect = [Reply(b"old"), Reply(b"new")]
    with patch.object(images, "async_get_clientsession", return_value=session):
        assert await image.async_image() == b"old"
        show(image, visitors, media(media_id, url))
        assert await image.async_image() == b"new"
        show(image, visitors, media(media_id, url))
        assert await image.async_image() == b"new"
    assert session.get.call_count == 2


async def test_concurrent_requests_download_once(hass):
    image, _, visitors = entity(hass)
    show(image, visitors, media())
    started, release = asyncio.Event(), asyncio.Event()
    session = MagicMock()
    session.get.return_value = Reply(b"shared", started=started, release=release)
    with patch.object(images, "async_get_clientsession", return_value=session):
        first = asyncio.create_task(image.async_image())
        await started.wait()
        second = asyncio.create_task(image.async_image())
        release.set()
        assert await asyncio.gather(first, second) == [b"shared", b"shared"]
    session.get.assert_called_once()


@pytest.mark.parametrize("clear", [True, False])
async def test_obsolete_inflight_response_is_discarded(hass, clear):
    image, _, visitors = entity(hass)
    show(image, visitors, media())
    started, release = asyncio.Event(), asyncio.Event()
    session = MagicMock()
    session.get.side_effect = [
        Reply(b"old", started=started, release=release),
        Reply(b"new"),
    ]
    with patch.object(images, "async_get_clientsession", return_value=session):
        request = asyncio.create_task(image.async_image())
        await started.wait()
        show(image, visitors, None if clear else media("replacement", URL))
        release.set()
        assert await request is None
        assert image._cached_image is None
        assert await image.async_image() == (None if clear else b"new")


async def test_unload_cancels_request_and_closes_response(hass):
    image, _, visitors = entity(hass)
    show(image, visitors, media())
    started, release = asyncio.Event(), asyncio.Event()
    reply = Reply(started=started, release=release)
    session = MagicMock()
    session.get.return_value = reply
    with patch.object(images, "async_get_clientsession", return_value=session):
        request = asyncio.create_task(image.async_image())
        await started.wait()
        await image.async_will_remove_from_hass()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert await image.async_image() is None
    assert reply.closed
    assert image._cached_image is None


@pytest.mark.parametrize(
    "suffix", ["", "?Expires=bad", "?Expires=", "?X-Amz-Date=bad&X-Amz-Expires=no"]
)
async def test_missing_or_malformed_expiry_is_safe(hass, suffix):
    image, _, visitors = entity(hass)
    show(image, visitors, media(url="https://cdn.example.invalid/still.jpg" + suffix))
    session = MagicMock()
    session.get.return_value = Reply()
    with patch.object(images, "async_get_clientsession", return_value=session):
        assert await image.async_image() == b"image"
    assert image.available


async def test_expired_bytes_refresh_only_bound_account(hass, monkeypatch):
    image, coordinator, visitors = entity(hass)
    other, other_coordinator, other_visitors = entity(hass)
    show(image, visitors, media())
    show(other, other_visitors, media())
    session = MagicMock()
    session.get.side_effect = [Reply(b"old"), Reply(b"fresh")]

    async def refresh(**_kwargs):
        visitors.latest_media = media(url=NEW_URL)

    visitors.async_update.side_effect = refresh
    with patch.object(images, "async_get_clientsession", return_value=session):
        assert await image.async_image() == b"old"
        monkeypatch.setattr(images, "time", lambda: 10000000000)
        assert await image.async_image() == b"fresh"
    visitors.async_update.assert_awaited_once_with(sole_feeder=True)
    other_visitors.async_update.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_called()
    coordinator.client.identify_postcard.assert_not_called()
    coordinator.client.collect_postcard.assert_not_called()
    other_coordinator.async_request_refresh.assert_not_called()


async def test_failed_refresh_backs_off_without_logging_urls(hass, caplog, monkeypatch):
    image, _, visitors = entity(hass)
    show(
        image,
        visitors,
        media(url="https://cdn.example.invalid/x?Expires=1&Signature=secret"),
    )
    visitors.async_update.side_effect = RuntimeError(f"Failed at {URL}")
    caplog.set_level("DEBUG", logger="custom_components.birdbuddy")
    clock = [0.0]
    monkeypatch.setattr(images, "monotonic", lambda: clock[0])
    assert await image.async_image() is None
    assert await image.async_image() is None
    visitors.async_update.assert_awaited_once()
    clock[0] = 61
    assert await image.async_image() is None
    assert visitors.async_update.await_count == 2
    assert "Signature" not in caplog.text
    assert "private" not in caplog.text


async def test_forbidden_url_refreshes_and_retries_rotated_url(hass, caplog):
    image, _, visitors = entity(hass)
    show(image, visitors, media())
    session = MagicMock()
    session.get.side_effect = [Reply(status=403), Reply(b"new")]

    async def refresh(**_kwargs):
        visitors.latest_media = media(url=NEW_URL)

    visitors.async_update.side_effect = refresh
    caplog.set_level("DEBUG", logger="custom_components.birdbuddy")
    with patch.object(images, "async_get_clientsession", return_value=session):
        assert await image.async_image() == b"new"
    visitors.async_update.assert_awaited_once()
    assert session.get.call_count == 2
    assert "Signature" not in caplog.text


async def test_unchanged_forbidden_url_does_not_retry_storm(hass):
    image, _, visitors = entity(hass)
    show(image, visitors, media())
    session = MagicMock()
    session.get.return_value = Reply(status=403)
    with patch.object(images, "async_get_clientsession", return_value=session):
        assert await image.async_image() is None
        assert await image.async_image() is None
    assert session.get.call_count == 1
    assert not image.available


async def test_video_thumbnail_only(hass):
    image, _, _ = entity(hass)
    image._update_url(
        media(
            url="https://cdn.example.invalid/video.mp4",
            __typename="MediaVideo",
            thumbnailUrl=URL,
        )
    )
    assert image.image_url == URL
    image._update_url(
        media(
            url="https://cdn.example.invalid/video.mp4",
            __typename="MediaVideo",
            thumbnailUrl=None,
        )
    )
    assert image.image_url is None
    assert not image.available


async def test_expired_content_falls_back_to_thumbnail(hass):
    image, _, _ = entity(hass)
    image._update_url(
        media(url="https://cdn.example.invalid/expired?Expires=1", thumbnailUrl=URL)
    )
    assert image.image_url == URL


@pytest.mark.parametrize("stamp", [None, "", "invalid", 10**100])
async def test_bad_timestamp_is_unknown_with_valid_image(hass, stamp):
    image, _, _ = entity(hass)
    image._update_url(media(createdAt=stamp))
    assert image.image_url == URL
    assert image.image_last_updated is None
    assert image.available


async def test_naive_timestamp_is_normalized(hass):
    image, _, _ = entity(hass)
    image._update_url(media(createdAt="2026-05-30T12:00:00"))
    assert image.image_last_updated == STAMP


async def test_unknown_expiry_cache_is_bounded(hass, monkeypatch):
    image, _, visitors = entity(hass)
    show(image, visitors, media(url="https://cdn.example.invalid/no-expiry.jpg"))
    clock = [0.0]
    monkeypatch.setattr(images, "monotonic", lambda: clock[0])
    session = MagicMock()
    session.get.side_effect = [Reply(b"first"), Reply(b"refetched")]
    with patch.object(images, "async_get_clientsession", return_value=session):
        assert await image.async_image() == b"first"
        clock[0] = images.MAX_CACHE_SECONDS + 1
        assert await image.async_image() == b"refetched"


async def test_carousel_slot_clears_independently(hass):
    first, coordinator, visitors = entity(hass)
    second = BirdBuddyIndexedRecentVisitorImageEntity(
        hass, first.feeder, coordinator, 2
    )
    second.hass = hass
    cast(Any, second).async_write_ha_state = MagicMock()
    visitors.latest_media = media()
    visitors.recent = [
        RecentVisitor(media(), None, STAMP),
        RecentVisitor(media("m2", NEW_URL), None, STAMP),
    ]
    first._on_recent_visitor(visitors)
    second._on_recent_visitor(visitors)
    session = MagicMock()
    session.get.side_effect = [Reply(b"first"), Reply(b"second")]
    with patch.object(images, "async_get_clientsession", return_value=session):
        assert await first.async_image() == b"first"
        assert await second.async_image() == b"second"
        visitors.recent = visitors.recent[:1]
        first._on_recent_visitor(visitors)
        second._on_recent_visitor(visitors)
        assert await first.async_image() == b"first"
        assert await second.async_image() is None
    assert first.available
    assert not second.available
    assert session.get.call_count == 2


async def test_carousel_slots_share_refresh_budget(hass):
    first, coordinator, visitors = entity(hass)
    second = BirdBuddyIndexedRecentVisitorImageEntity(
        hass, first.feeder, coordinator, 2
    )
    second.hass = hass
    cast(Any, second).async_write_ha_state = MagicMock()
    item = media(url="https://cdn.example.invalid/expired?Expires=1")
    show(first, visitors, item)
    second._on_recent_visitor(visitors)
    started, release = asyncio.Event(), asyncio.Event()

    async def refresh(**_kwargs):
        started.set()
        await release.wait()

    visitors.async_update.side_effect = refresh
    request = asyncio.create_task(first.async_image())
    await started.wait()
    other = asyncio.create_task(second.async_image())
    release.set()
    assert await asyncio.gather(request, other) == [None, None]
    visitors.async_update.assert_awaited_once()


async def test_url_expiring_during_download_is_discarded(hass, monkeypatch):
    image, _, visitors = entity(hass)
    show(image, visitors, media())
    started, release = asyncio.Event(), asyncio.Event()
    session = MagicMock()
    session.get.return_value = Reply(started=started, release=release)
    with patch.object(images, "async_get_clientsession", return_value=session):
        request = asyncio.create_task(image.async_image())
        await started.wait()
        monkeypatch.setattr(images, "time", lambda: 10000000000)
        release.set()
        assert await request is None
    assert image.image_url is None
    assert image._cached_image is None
    assert not image.available


@pytest.mark.parametrize("content_type", ["text/html", "application/json", "video/mp4"])
async def test_nonimage_response_is_unavailable(hass, content_type):
    image, _, visitors = entity(hass)
    show(image, visitors, media())
    session = MagicMock()
    session.get.return_value = Reply(content_type=content_type)
    with patch.object(images, "async_get_clientsession", return_value=session):
        assert await image.async_image() is None
        assert await image.async_image() is None
    assert not image.available
    assert image._cached_image is None
    session.get.assert_called_once()


async def test_refresh_cancellation_propagates(hass):
    image, _, visitors = entity(hass)
    show(image, visitors, media(url="https://cdn.example.invalid/x?Expires=1"))
    started, release = asyncio.Event(), asyncio.Event()

    async def refresh(**_kwargs):
        started.set()
        await release.wait()

    visitors.async_update.side_effect = refresh
    request = asyncio.create_task(image.async_image())
    await started.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert image._cached_image is None
    assert not image.available
