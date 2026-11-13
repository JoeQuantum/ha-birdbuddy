"""Tests for the Last Sync sensor and its coordinator timestamp."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from birdbuddy.feed import Feed
from birdbuddy.user import BirdBuddyUser
from tests.ha_compat import common as ha_test_common

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.birdbuddy.coordinator import BirdBuddyDataUpdateCoordinator
from custom_components.birdbuddy.sensor import BirdBuddyLastSyncEntity


def _coordinator(hass) -> BirdBuddyDataUpdateCoordinator:
    entry = ha_test_common.MockConfigEntry(
        domain="birdbuddy", data={"account_id": "account-a"}
    )
    client = MagicMock()
    client.refresh = AsyncMock(return_value=True)
    client.user = BirdBuddyUser({"id": "account-a"})
    client.feeders = {}
    client.feed = AsyncMock(
        return_value=Feed(
            {"edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        )
    )
    c = BirdBuddyDataUpdateCoordinator(hass, client, entry)
    c.intake = MagicMock(initialized=True, dead_letter_count=0)
    c.intake.async_dispatch = AsyncMock()
    c.intake.async_close = AsyncMock()
    c._accept_pending = AsyncMock()
    return c


async def test_last_update_timestamp_set_on_success(hass) -> None:
    """A successful telemetry poll stamps a tz-aware UTC timestamp."""
    c = _coordinator(hass)
    result = await c._async_update_data()
    assert result is c.client
    assert isinstance(c.last_update_timestamp, datetime)
    assert c.last_update_timestamp.tzinfo is not None
    await c.async_shutdown()


async def test_last_update_timestamp_unchanged_on_failure(hass) -> None:
    """A failed telemetry poll must preserve the previous successful timestamp."""
    c = _coordinator(hass)
    previous = datetime(2026, 1, 1, tzinfo=timezone.utc)
    c.last_update_timestamp = previous
    c.client.refresh = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(UpdateFailed):
        await c._async_update_data()
    assert c.last_update_timestamp is previous
    await c.async_shutdown()


def test_sensor_reports_coordinator_timestamp() -> None:
    """The sensor's value mirrors the coordinator's last_update_timestamp."""
    sensor = object.__new__(BirdBuddyLastSyncEntity)
    coordinator = MagicMock()
    ts = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
    coordinator.last_update_timestamp = ts
    sensor.coordinator = coordinator
    assert sensor.native_value is ts
