"""Tests for custom_components.birdbuddy_plus.coordinator."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from birdbuddy.exceptions import GraphqlError
from birdbuddy.feed import FeedNodeType

from custom_components.birdbuddy_plus.const import EVENT_NEW_POSTCARD_SIGHTING
from custom_components.birdbuddy_plus.coordinator import BirdBuddyDataUpdateCoordinator


def _make_coordinator(*, listener_count: int = 1) -> BirdBuddyDataUpdateCoordinator:
    """Build a coordinator without engaging DataUpdateCoordinator/HA init.

    We only exercise `_process_feed` here, which touches `self.client`,
    `self.hass.bus`, and the module-level LOGGER. Seed those and skip the
    parent __init__.
    """
    coordinator = object.__new__(BirdBuddyDataUpdateCoordinator)
    coordinator.client = MagicMock()
    coordinator.hass = MagicMock()
    coordinator.hass.bus.async_listeners.return_value = {
        EVENT_NEW_POSTCARD_SIGHTING: listener_count,
    }
    return coordinator


def _postcard(node_id: str) -> MagicMock:
    p = MagicMock()
    p.node_id = node_id
    p.node_type = FeedNodeType.NewPostcard
    p.data = {"id": node_id, "__typename": "FeedItemNewPostcard"}
    return p


def _sighting(feeder_id: str = "f1") -> MagicMock:
    s = MagicMock()
    s.data = {
        "feeder": {"id": feeder_id, "name": "Backyard"},
        "sightingReport": {"reportToken": "", "sightings": []},
    }
    return s


def test_failing_postcard_doesnt_kill_subsequent_postcards() -> None:
    """Regression for upstream #98.

    Bird Buddy now requires manual identification in the BB app before a
    sighting can be created from a postcard; until then,
    `sightingCreateFromPostcard` returns INTERNAL_SERVER_ERROR. Before this
    fix, that error propagated up to `_async_update_data` and was converted
    to UpdateFailed, losing every other postcard in the same cycle (and
    permanently — `refresh_feed` had already advanced its cursor past them).

    After the fix, the failing postcard is logged and skipped; remaining
    postcards in the same cycle are still processed; no exception escapes.
    """
    coordinator = _make_coordinator()

    bad = _postcard("bad-postcard")
    good = _postcard("good-postcard")

    async def fake_sighting_from_postcard(*, postcard):
        if postcard.node_id == "bad-postcard":
            raise GraphqlError(
                {
                    "message": "Internal server error.",
                    "path": ["sightingCreateFromPostcard"],
                    "extensions": {"code": "INTERNAL_SERVER_ERROR"},
                }
            )
        return _sighting()

    coordinator.client.sighting_from_postcard = AsyncMock(
        side_effect=fake_sighting_from_postcard
    )

    # Must not raise.
    asyncio.run(coordinator._process_feed([bad, good]))

    # The bad postcard's error was swallowed; the good one fired an event.
    assert coordinator.client.sighting_from_postcard.await_count == 2
    fires = coordinator.hass.bus.fire.call_args_list
    assert len(fires) == 1, f"expected 1 event fired, got {len(fires)}: {fires}"
    fired_data = fires[0].kwargs["event_data"]
    assert fired_data["postcard"]["id"] == "good-postcard"


def test_failing_postcard_doesnt_raise() -> None:
    """If every postcard in a cycle fails, _process_feed still must not raise
    (so the coordinator doesn't enter UpdateFailed and disable the integration
    for normal feeder/state updates that have nothing to do with postcards)."""
    coordinator = _make_coordinator()

    coordinator.client.sighting_from_postcard = AsyncMock(
        side_effect=GraphqlError(
            {
                "message": "Internal server error.",
                "path": ["sightingCreateFromPostcard"],
                "extensions": {"code": "INTERNAL_SERVER_ERROR"},
            }
        )
    )

    asyncio.run(coordinator._process_feed([_postcard("p1"), _postcard("p2")]))

    assert coordinator.client.sighting_from_postcard.await_count == 2
    coordinator.hass.bus.fire.assert_not_called()


def test_no_listeners_skips_sighting_conversion() -> None:
    """If no automation is listening for the event, we shouldn't call
    sightingCreateFromPostcard at all (saves an API call AND avoids the #98
    error entirely if the user isn't using auto-collection). Pre-existing
    behavior we want to verify is preserved."""
    coordinator = _make_coordinator(listener_count=0)
    coordinator.hass.bus.async_listeners.return_value = {}
    coordinator.client.sighting_from_postcard = AsyncMock(return_value=_sighting())

    asyncio.run(coordinator._process_feed([_postcard("p1")]))

    coordinator.client.sighting_from_postcard.assert_not_called()
    coordinator.hass.bus.fire.assert_not_called()
