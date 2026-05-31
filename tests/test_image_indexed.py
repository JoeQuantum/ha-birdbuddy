"""Tests for the indexed carousel image entity."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.birdbuddy.image import (
    BirdBuddyIndexedRecentVisitorImageEntity,
)
from custom_components.birdbuddy.visitors import RecentVisitor


def _make_indexed(index: int) -> BirdBuddyIndexedRecentVisitorImageEntity:
    """Build the indexed entity without engaging ImageEntity/CoordinatorEntity
    init (mirrors the pattern in test_sensor / test_coordinator)."""
    entity = object.__new__(BirdBuddyIndexedRecentVisitorImageEntity)
    entity._index = index
    entity._attr_image_url = None
    entity._attr_image_last_updated = None
    entity._attr_entity_picture = None
    entity._cached_image = None
    entity.feeder = SimpleNamespace(id="f1", name="Backyard")
    return entity


def _entry(url: str, *, species: str | None = None) -> RecentVisitor:
    return RecentVisitor(
        media=SimpleNamespace(content_url=url, thumbnail_url=url),
        species=SimpleNamespace(name=species) if species else None,
        created_at=datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc),
    )


def _visitors(recent: list[RecentVisitor]) -> MagicMock:
    v = MagicMock()
    v.recent = recent
    return v


def test_indexed_entity_picks_correct_position() -> None:
    """Index 2 must pick `recent[1]`, index 3 → `recent[2]`, etc. — the
    user-visible numbering is 1-based but `recent` is 0-based."""
    recent = [
        _entry("https://cdn.example.com/0.jpg", species="Cardinal"),
        _entry("https://cdn.example.com/1.jpg", species="Goldfinch"),
        _entry("https://cdn.example.com/2.jpg", species="Bluejay"),
    ]

    entity2 = _make_indexed(2)
    with patch.object(
        BirdBuddyIndexedRecentVisitorImageEntity, "async_write_ha_state"
    ):
        entity2._on_recent_visitor(_visitors(recent))
    assert entity2._attr_image_url == "https://cdn.example.com/1.jpg"

    entity3 = _make_indexed(3)
    with patch.object(
        BirdBuddyIndexedRecentVisitorImageEntity, "async_write_ha_state"
    ):
        entity3._on_recent_visitor(_visitors(recent))
    assert entity3._attr_image_url == "https://cdn.example.com/2.jpg"


def test_indexed_entity_clears_when_position_unavailable() -> None:
    """If the feed currently has fewer entries than this entity's index
    (e.g. quiet feeder, only 2 recent visits), the entity must clear its
    URL rather than retain a stale one from a busier period."""
    entity = _make_indexed(5)
    entity._attr_image_url = "https://cdn.example.com/stale.jpg"
    entity._attr_entity_picture = entity._attr_image_url

    recent = [_entry("https://cdn.example.com/0.jpg", species="Cardinal")]

    with patch.object(
        BirdBuddyIndexedRecentVisitorImageEntity, "async_write_ha_state"
    ):
        entity._on_recent_visitor(_visitors(recent))

    assert entity._attr_image_url is None
    assert entity._attr_entity_picture is None
    assert entity._attr_image_last_updated is None


def test_indexed_entity_empty_recent_is_safe() -> None:
    """No recent visitors at all (cold start, empty feed): must clear and
    not raise."""
    entity = _make_indexed(2)

    with patch.object(
        BirdBuddyIndexedRecentVisitorImageEntity, "async_write_ha_state"
    ):
        entity._on_recent_visitor(_visitors([]))

    assert entity._attr_image_url is None


def test_indexed_entity_unique_id_and_name_follow_index() -> None:
    """The user-facing convention: position N gets `-recent-image-N` unique
    ID and `Recent Visitor Image N` name. Position 1 is owned by the
    existing latest entity (not this class)."""
    feeder = SimpleNamespace(
        id="abc123",
        name="Backyard",
        device_info={"identifiers": {("birdbuddy", "abc123")}},
    )

    def _fake_mixin_init(self, feeder, coordinator):
        self.feeder = feeder
        self.coordinator = coordinator
        self._attr_device_info = feeder.device_info

    with patch(
        "custom_components.birdbuddy.image.ImageEntity.__init__", return_value=None
    ), patch(
        "custom_components.birdbuddy.entity.BirdBuddyMixin.__init__",
        _fake_mixin_init,
    ):
        entity = BirdBuddyIndexedRecentVisitorImageEntity(
            MagicMock(), feeder, MagicMock(), index=3
        )

    assert entity._attr_unique_id == "abc123-recent-image-3"
    assert entity._attr_name == "Recent Visitor Image 3"
    assert entity._index == 3
    # Disabled by default. Read via instance because HA's cached_properties
    # metaclass renames `_attr_*` class attrs (one leading underscore becomes
    # two) so class-level lookup gives back the descriptor itself.
    assert entity._attr_entity_registry_enabled_default is False
