"""Tests for the Last Sync sensor and its coordinator timestamp."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.birdbuddy.coordinator import BirdBuddyDataUpdateCoordinator
from custom_components.birdbuddy.sensor import BirdBuddyLastSyncEntity


def _coordinator() -> BirdBuddyDataUpdateCoordinator:
    """A coordinator wired just enough to run one _async_update_data."""
    c = object.__new__(BirdBuddyDataUpdateCoordinator)
    c.client = MagicMock()
    c.client.refresh = AsyncMock(return_value=True)
    c.client.feeders = {"f1": {"id": "f1", "name": "Backyard"}}
    c.hass = MagicMock()
    c.feeders = {}
    c.visitors = {}
    c.first_update = True
    c.last_update_timestamp = None
    return c


def test_last_update_timestamp_set_on_success() -> None:
    """A successful poll stamps a tz-aware UTC timestamp."""
    c = _coordinator()
    result = asyncio.run(c._async_update_data())
    assert result is c.client
    assert isinstance(c.last_update_timestamp, datetime)
    assert c.last_update_timestamp.tzinfo is not None


def test_last_update_timestamp_unchanged_on_failure() -> None:
    """A failed poll must not advance the timestamp."""
    c = _coordinator()
    c.client.refresh = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(UpdateFailed):
        asyncio.run(c._async_update_data())
    assert c.last_update_timestamp is None


def test_sensor_reports_coordinator_timestamp() -> None:
    """The sensor's value mirrors the coordinator's last_update_timestamp."""
    sensor = object.__new__(BirdBuddyLastSyncEntity)
    coordinator = MagicMock()
    ts = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)
    coordinator.last_update_timestamp = ts
    sensor.coordinator = coordinator
    assert sensor.native_value is ts
