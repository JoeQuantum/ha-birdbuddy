"""Tests for custom_components.birdbuddy.sensor."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.birdbuddy.sensor import BirdBuddyRecentVisitorEntity
from custom_components.birdbuddy.visitors import RecentVisitor


def _make_sensor() -> BirdBuddyRecentVisitorEntity:
    """Build a recent_visitor sensor without triggering CoordinatorEntity/HA init.

    We only exercise `_on_recent_visitor` here, so we can skip __init__ and
    seed the attributes the callback touches.
    """
    sensor = object.__new__(BirdBuddyRecentVisitorEntity)
    sensor._latest_media = None
    sensor._attr_entity_picture = None
    sensor._attr_native_value = None
    sensor._recent_visitors = []
    return sensor


def _media(url: str) -> SimpleNamespace:
    return SimpleNamespace(content_url=url, thumbnail_url=url)


def _species(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _visitors(media, species, recent: list | None = None) -> MagicMock:
    v = MagicMock()
    v.latest_media = media
    v.latest_species = species
    v.recent = recent if recent is not None else []
    return v


def test_state_and_picture_update_together_on_recognized_postcard() -> None:
    """Baseline: when a recognized bird arrives, both update to that sighting."""
    sensor = _make_sensor()
    with patch.object(BirdBuddyRecentVisitorEntity, "async_write_ha_state"):
        sensor._on_recent_visitor(_visitors(_media("cardinal.jpg"), _species("Cardinal")))
    assert sensor._attr_native_value == "Cardinal"
    assert sensor._attr_entity_picture == "cardinal.jpg"


def test_state_clears_with_new_media_when_species_unrecognized() -> None:
    """Regression for upstream #95.

    The previous behavior was: if a new postcard arrives but the bird is
    unrecognized (no unlocked / recognized / suggested species), the visitor
    handler sets `latest_species` to None. The sensor used to update
    `entity_picture` from the new media but skip the state update (`if species:`
    guard), so state showed the **previous** sighting's species while the
    picture already showed the new one — the "off by one" lag.

    After the fix, state and entity_picture must always describe the same
    sighting. When the new sighting is unrecognized, state becomes None
    rather than going stale.
    """
    sensor = _make_sensor()

    # Sighting 1: recognized bird → both populated.
    with patch.object(BirdBuddyRecentVisitorEntity, "async_write_ha_state"):
        sensor._on_recent_visitor(
            _visitors(_media("cardinal.jpg"), _species("Cardinal"))
        )
    assert sensor._attr_native_value == "Cardinal"
    assert sensor._attr_entity_picture == "cardinal.jpg"

    # Sighting 2: new image but no species detected.
    with patch.object(BirdBuddyRecentVisitorEntity, "async_write_ha_state"):
        sensor._on_recent_visitor(_visitors(_media("blurry.jpg"), None))

    # Picture must be the new sighting's image (this already worked).
    assert sensor._attr_entity_picture == "blurry.jpg"
    # State must NOT still be "Cardinal" — that would be the #95 lag bug.
    # It can be None ("we saw something but don't know what"); the contract
    # this test enforces is that state and picture describe the same sighting.
    assert sensor._attr_native_value is None


def test_species_only_update_preserves_picture() -> None:
    """The collections-fallback path in `_update_latest_visitor` can deliver a
    species update without a corresponding new media. State should update;
    entity_picture should stay on the previous sighting's image."""
    sensor = _make_sensor()

    with patch.object(BirdBuddyRecentVisitorEntity, "async_write_ha_state"):
        sensor._on_recent_visitor(
            _visitors(_media("first.jpg"), _species("Cardinal"))
        )

    # Now species arrives without media (e.g. backfilled from collections).
    with patch.object(BirdBuddyRecentVisitorEntity, "async_write_ha_state"):
        sensor._on_recent_visitor(_visitors(None, _species("Goldfinch")))

    assert sensor._attr_native_value == "Goldfinch"
    assert sensor._attr_entity_picture == "first.jpg"


def test_empty_visitors_is_a_noop() -> None:
    """When neither media nor species is present (e.g. initial register_callback
    fires before any data arrives), the callback should not change state."""
    sensor = _make_sensor()
    sensor._attr_native_value = "Previous"
    sensor._attr_entity_picture = "previous.jpg"

    with patch.object(BirdBuddyRecentVisitorEntity, "async_write_ha_state"):
        sensor._on_recent_visitor(_visitors(None, None))

    assert sensor._attr_native_value == "Previous"
    assert sensor._attr_entity_picture == "previous.jpg"


def _recent_entry(species: str | None, url: str, when: datetime) -> RecentVisitor:
    return RecentVisitor(
        media=SimpleNamespace(content_url=url, thumbnail_url=url),
        species=SimpleNamespace(name=species) if species else None,
        created_at=when,
    )


def test_visitors_attribute_populated_from_recent_list() -> None:
    """The carousel surface: sensor must expose `visitors` (list of
    `{species, media_url, created_at}`) so dashboards/templates can read
    the full last-N feed without subscribing to each indexed image entity."""
    sensor = _make_sensor()
    base = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    recent = [
        _recent_entry("Cardinal", "https://cdn.example.com/0.jpg", base),
        _recent_entry("Goldfinch", "https://cdn.example.com/1.jpg", base.replace(hour=11)),
        _recent_entry(None, "https://cdn.example.com/2.jpg", base.replace(hour=10)),
    ]

    with patch.object(BirdBuddyRecentVisitorEntity, "async_write_ha_state"):
        sensor._on_recent_visitor(_visitors(recent[0].media, recent[0].species, recent))

    attrs = sensor.extra_state_attributes
    assert "visitors" in attrs
    assert attrs["visitors"] == [
        {
            "species": "Cardinal",
            "media_url": "https://cdn.example.com/0.jpg",
            "created_at": base.isoformat(),
        },
        {
            "species": "Goldfinch",
            "media_url": "https://cdn.example.com/1.jpg",
            "created_at": base.replace(hour=11).isoformat(),
        },
        {
            "species": None,  # unrecognized sighting in the feed
            "media_url": "https://cdn.example.com/2.jpg",
            "created_at": base.replace(hour=10).isoformat(),
        },
    ]


def test_visitors_attribute_empty_when_no_recent_data() -> None:
    """Cold start (no visitors yet): attribute exists and is an empty list,
    so consumers can rely on the key being present."""
    sensor = _make_sensor()
    attrs = sensor.extra_state_attributes
    assert attrs == {"visitors": []}
