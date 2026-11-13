"""Tests for custom_components.birdbuddy.image."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.image import ImageEntity
from homeassistant.helpers.typing import UndefinedType

from custom_components.birdbuddy.image import BirdBuddyRecentVisitorImageEntity

IMAGE_TIME = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _make_image_entity(
    *,
    coordinator: MagicMock | None = None,
    feeder_id: str = "f1",
) -> BirdBuddyRecentVisitorImageEntity:
    """Build the image entity without engaging ImageEntity/CoordinatorEntity init."""
    entity = object.__new__(BirdBuddyRecentVisitorImageEntity)
    entity._latest_media = None
    entity._attr_image_url = None
    entity._attr_image_last_updated = None
    entity._attr_entity_picture = None
    entity._cached_image = None
    entity.coordinator = coordinator or MagicMock()
    entity.feeder = SimpleNamespace(id=feeder_id, name="Backyard")
    return entity


def _media(url: str, created_at: object = "2026-05-30T12:00:00") -> SimpleNamespace:
    return SimpleNamespace(content_url=url, thumbnail_url=url, created_at=created_at)


def test_handle_coordinator_update_clears_expired_url() -> None:
    """Without a new visitor event, an expired signed URL must be cleared on
    coordinator update so HA stops trying to fetch a CloudFront URL that will
    400/403."""
    entity = _make_image_entity()
    entity._attr_image_url = "https://cdn.example.com/bird.jpg?Expires=1"
    entity._attr_entity_picture = entity._attr_image_url
    entity._attr_image_last_updated = IMAGE_TIME

    with (
        patch("custom_components.birdbuddy.image.is_media_expired", return_value=True),
        patch(
            "custom_components.birdbuddy.entity.BirdBuddyMixin._handle_coordinator_update"
        ),
    ):
        entity._handle_coordinator_update()

    assert entity._attr_image_url is None
    assert entity._attr_image_last_updated is None
    assert entity._attr_entity_picture is None


def test_handle_coordinator_update_keeps_fresh_url() -> None:
    """A still-fresh URL must survive the coordinator tick untouched."""
    entity = _make_image_entity()
    fresh_url = "https://cdn.example.com/bird.jpg?Expires=99999999999"
    entity._attr_image_url = fresh_url
    entity._attr_entity_picture = fresh_url
    entity._attr_image_last_updated = IMAGE_TIME

    with (
        patch("custom_components.birdbuddy.image.is_media_expired", return_value=False),
        patch(
            "custom_components.birdbuddy.entity.BirdBuddyMixin._handle_coordinator_update"
        ),
    ):
        entity._handle_coordinator_update()

    assert entity._attr_image_url == fresh_url
    assert entity._attr_entity_picture == fresh_url
    assert entity._attr_image_last_updated == IMAGE_TIME


@pytest.mark.parametrize("url", [object.__new__(ImageEntity).image_url, None, ""])
def test_handle_coordinator_update_noop_without_url(
    url: str | None | UndefinedType,
) -> None:
    """An unset URL must not be passed to the signed-URL expiry parser."""
    entity = _make_image_entity()
    entity._attr_image_url = url

    with (
        patch("custom_components.birdbuddy.image.is_media_expired") as mock_expired,
        patch(
            "custom_components.birdbuddy.entity.BirdBuddyMixin._handle_coordinator_update"
        ),
    ):
        entity._handle_coordinator_update()

    mock_expired.assert_not_called()
    assert entity._attr_image_url is url


@pytest.mark.parametrize("url", [object.__new__(ImageEntity).image_url, None, ""])
def test_update_url_clears_without_media_or_url(
    url: str | None | UndefinedType,
) -> None:
    """A visitor without media explicitly clears all image state."""
    entity = _make_image_entity()
    entity._attr_image_url = url

    with patch("custom_components.birdbuddy.image.is_media_expired") as mock_expired:
        entity._update_url(None)

    mock_expired.assert_not_called()
    assert entity._attr_image_url is None


def test_update_url_clears_expired_url_without_new_media() -> None:
    """A visitor event without a replacement must still clear an expired URL."""
    entity = _make_image_entity()
    entity._attr_image_url = "https://cdn.example.com/bird.jpg?Expires=1"
    entity._attr_entity_picture = entity._attr_image_url
    entity._attr_image_last_updated = IMAGE_TIME

    with patch(
        "custom_components.birdbuddy.image.is_media_expired", return_value=True
    ) as mock_expired:
        entity._update_url(None)

    mock_expired.assert_not_called()
    assert entity._attr_image_url is None
    assert entity._attr_image_last_updated is None
    assert entity._attr_entity_picture is None


def test_async_added_to_hass_seeds_from_existing_coordinator_state() -> None:
    """On add, if the coordinator already has a populated RecentVisitors for
    this feeder, the entity must adopt that media instead of staying blank
    until the next event."""
    coordinator = MagicMock()
    fresh_url = "https://cdn.example.com/bird.jpg?Expires=99999999999"
    media = _media(fresh_url, created_at="2026-05-30T12:00:00")
    coordinator.visitors = {"f1": SimpleNamespace(latest_media=media)}
    coordinator.add_visitor_listener.return_value = lambda: None

    entity = _make_image_entity(coordinator=coordinator)

    with (
        patch("custom_components.birdbuddy.image.is_media_expired", return_value=False),
        patch.object(BirdBuddyRecentVisitorImageEntity, "async_on_remove"),
        patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            return_value=None,
        ),
    ):
        asyncio.run(entity.async_added_to_hass())

    assert entity._attr_image_url == fresh_url
    assert entity._attr_entity_picture == fresh_url
    assert entity._attr_image_last_updated == IMAGE_TIME
    coordinator.add_visitor_listener.assert_called_once()


def test_async_added_to_hass_handles_missing_visitor_state() -> None:
    """Cold start: no RecentVisitors entry yet for this feeder. Must register
    the listener and not raise."""
    coordinator = MagicMock()
    coordinator.visitors = {}
    coordinator.add_visitor_listener.return_value = lambda: None

    entity = _make_image_entity(coordinator=coordinator)

    with (
        patch.object(BirdBuddyRecentVisitorImageEntity, "async_on_remove"),
        patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            return_value=None,
        ),
    ):
        asyncio.run(entity.async_added_to_hass())

    assert entity._attr_image_url is None
    coordinator.add_visitor_listener.assert_called_once()


def test_async_added_to_hass_clears_when_media_is_none() -> None:
    """RecentVisitors may exist but have no media yet (e.g. listening but
    _update_latest_visitor hasn't completed). Explicitly clear the image."""
    coordinator = MagicMock()
    coordinator.visitors = {"f1": SimpleNamespace(latest_media=None)}
    coordinator.add_visitor_listener.return_value = lambda: None

    entity = _make_image_entity(coordinator=coordinator)

    with (
        patch.object(BirdBuddyRecentVisitorImageEntity, "_update_url") as mock_update,
        patch.object(BirdBuddyRecentVisitorImageEntity, "async_on_remove"),
        patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            return_value=None,
        ),
    ):
        asyncio.run(entity.async_added_to_hass())

    mock_update.assert_called_once_with(None)
    coordinator.add_visitor_listener.assert_called_once()
