"""Tests for custom_components.birdbuddy_plus.sensor."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.birdbuddy_plus.sensor import BirdBuddyRecentVisitorEntity


def _make_sensor() -> BirdBuddyRecentVisitorEntity:
    """Build a recent_visitor sensor without triggering CoordinatorEntity/HA init.

    We only exercise `_on_recent_visitor` here, so we can skip __init__ and
    seed the attributes the callback touches.
    """
    sensor = object.__new__(BirdBuddyRecentVisitorEntity)
    sensor._latest_media = None
    sensor._attr_entity_picture = None
    sensor._attr_native_value = None
    return sensor


def _media(url: str) -> SimpleNamespace:
    return SimpleNamespace(content_url=url, thumbnail_url=url)


def _species(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _visitors(media, species) -> MagicMock:
    v = MagicMock()
    v.latest_media = media
    v.latest_species = species
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
