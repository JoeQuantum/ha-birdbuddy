"""Tests for custom_components.birdbuddy.image."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from homeassistant.components.image import UNDEFINED

from custom_components.birdbuddy.image import BirdBuddyRecentVisitorImageEntity


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
    entity._attr_image_last_updated = "old"

    with patch(
        "custom_components.birdbuddy.image.is_media_expired", return_value=True
    ), patch(
        "custom_components.birdbuddy.entity.BirdBuddyMixin._handle_coordinator_update"
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
    entity._attr_image_last_updated = "now"

    with patch(
        "custom_components.birdbuddy.image.is_media_expired", return_value=False
    ), patch(
        "custom_components.birdbuddy.entity.BirdBuddyMixin._handle_coordinator_update"
    ):
        entity._handle_coordinator_update()

    assert entity._attr_image_url == fresh_url
    assert entity._attr_entity_picture == fresh_url
    assert entity._attr_image_last_updated == "now"


def test_handle_coordinator_update_noop_when_url_undefined() -> None:
    """A never-populated URL (UNDEFINED sentinel) must not be touched and must
    not raise."""
    entity = _make_image_entity()
    entity._attr_image_url = UNDEFINED

    with patch(
        "custom_components.birdbuddy.image.is_media_expired"
    ) as mock_expired, patch(
        "custom_components.birdbuddy.entity.BirdBuddyMixin._handle_coordinator_update"
    ):
        entity._handle_coordinator_update()

    mock_expired.assert_not_called()
    assert entity._attr_image_url is UNDEFINED


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

    with patch(
        "custom_components.birdbuddy.image.is_media_expired", return_value=False
    ), patch.object(
        BirdBuddyRecentVisitorImageEntity, "async_on_remove"
    ), patch(
        "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
        return_value=None,
    ):
        asyncio.run(entity.async_added_to_hass())

    assert entity._attr_image_url == fresh_url
    assert entity._attr_entity_picture == fresh_url
    assert entity._attr_image_last_updated == "2026-05-30T12:00:00"
    coordinator.add_visitor_listener.assert_called_once()


def test_async_added_to_hass_handles_missing_visitor_state() -> None:
    """Cold start: no RecentVisitors entry yet for this feeder. Must register
    the listener and not raise."""
    coordinator = MagicMock()
    coordinator.visitors = {}
    coordinator.add_visitor_listener.return_value = lambda: None

    entity = _make_image_entity(coordinator=coordinator)

    with patch.object(BirdBuddyRecentVisitorImageEntity, "async_on_remove"), patch(
        "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
        return_value=None,
    ):
        asyncio.run(entity.async_added_to_hass())

    assert entity._attr_image_url is None
    coordinator.add_visitor_listener.assert_called_once()


def test_async_added_to_hass_skips_seeding_when_media_is_none() -> None:
    """RecentVisitors may exist but have no media yet (e.g. listening but
    _update_latest_visitor hasn't completed). Must not call _update_url with
    None."""
    coordinator = MagicMock()
    coordinator.visitors = {"f1": SimpleNamespace(latest_media=None)}
    coordinator.add_visitor_listener.return_value = lambda: None

    entity = _make_image_entity(coordinator=coordinator)

    with patch.object(
        BirdBuddyRecentVisitorImageEntity, "_update_url"
    ) as mock_update, patch.object(
        BirdBuddyRecentVisitorImageEntity, "async_on_remove"
    ), patch(
        "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
        return_value=None,
    ):
        asyncio.run(entity.async_added_to_hass())

    mock_update.assert_not_called()
    coordinator.add_visitor_listener.assert_called_once()
