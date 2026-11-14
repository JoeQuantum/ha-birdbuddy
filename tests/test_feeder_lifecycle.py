"""Feeder discovery and availability preserve Home Assistant registry identity."""

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from birdbuddy.feed import Feed
from birdbuddy.feeder import Feeder
from birdbuddy.user import BirdBuddyUser
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.birdbuddy.const import DOMAIN
from custom_components.birdbuddy.device import BirdBuddyDevice
from custom_components.birdbuddy.select import BirdBuddyPowerProfileSelector
from custom_components.birdbuddy.sensor import BirdBuddyStateEntity
from custom_components.birdbuddy.switch import (
    BirdBuddyAudioSwitch,
    BirdBuddyOffGridSwitch,
)
from tests.ha_compat import common as ha_test_common


def _feeder(role="FeederForOwner", **extra):
    data: dict[str, Any] = {"id": "f1", "name": "Fixture feeder", "__typename": role}
    if role != "FeederForMemberPending":
        data.update(
            {
                "state": "ONLINE",
                "battery": {"percentage": 85, "charging": False},
                "signal": {"value": -50},
            }
        )
    if role == "FeederForOwner":
        data.update(
            {
                "supportsAudio": True,
                "audioEnabled": True,
                "offGrid": False,
                "powerProfile": "STANDARD_MODE",
                "firmwareVersion": "1.0",
            }
        )
    data.update(extra)
    return data


@pytest.fixture
def hass_storage():
    yield {}


@pytest.fixture
def hass_config_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
async def account(hass):
    entry = ha_test_common.MockConfigEntry(
        domain=DOMAIN,
        data={"email": "account@example.invalid", "password": "fixture-password"},
        state=ConfigEntryState.NOT_LOADED,
    )
    entry.add_to_hass(hass)
    feeders = {}

    async def feed_page(**kwargs):
        assert kwargs["query"].lstrip().startswith("query")
        return {
            "me": {
                "feed": {
                    "edges": [],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }

    with (
        patch(
            "birdbuddy.client.BirdBuddy._make_request",
            new=AsyncMock(side_effect=feed_page),
        ),
        patch(
            "birdbuddy.client.BirdBuddy.refresh", new=AsyncMock(return_value=True)
        ) as refresh,
        patch(
            "birdbuddy.client.BirdBuddy.user",
            new_callable=PropertyMock,
            return_value=BirdBuddyUser(
                {"id": "account", "email": "account@example.invalid"}
            ),
        ),
        patch(
            "birdbuddy.client.BirdBuddy.feeders",
            new_callable=PropertyMock,
            return_value=feeders,
        ),
        patch(
            "birdbuddy.client.BirdBuddy.feed",
            new=AsyncMock(
                return_value=Feed(
                    {"edges": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
                )
            ),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator.feed_error_type is None
        yield entry, coordinator, feeders, refresh
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert not coordinator._listeners


async def _refresh(hass, coordinator, feeders, value):
    feeders.clear()
    if value is not None:
        feeders["f1"] = deepcopy(value)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.feed_error_type is None


def _entries(registry, entry):
    return {
        item.unique_id: item
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


async def test_zero_pending_member_owner_removed_and_reappeared(hass, account):
    entry, coordinator, feeders, _ = account
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    assert coordinator.feeders == {}
    assert not _entries(registry, entry)
    assert coordinator._listeners  # platform listeners keep discovery alive at zero

    # Preserve an existing user choice before this device is discovered.
    battery = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "f1-battery",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    await _refresh(hass, coordinator, feeders, _feeder("FeederForMemberPending"))
    assert set(_entries(registry, entry)) == {"f1-battery"}

    await _refresh(hass, coordinator, feeders, _feeder("FeederForMember"))
    member_entries = _entries(registry, entry)
    assert "f1-state" in member_entries
    assert "f1-recent-image-5" in member_entries
    assert (
        not {"f1-audio", "f1-offgrid", "f1-power-profile", "f1-updater"}
        & member_entries.keys()
    )
    state_id = member_entries["f1-state"].entity_id
    assert hass.states.get(state_id).state == "online"
    device_id = member_entries["f1-state"].device_id
    assert device_id is not None
    assert devices.async_get(device_id) is not None
    existing_ids = {key: item.entity_id for key, item in member_entries.items()}

    await _refresh(hass, coordinator, feeders, _feeder(supportsAudio=False))
    owner_entries = _entries(registry, entry)
    assert {"f1-offgrid", "f1-power-profile", "f1-updater"} <= owner_entries.keys()
    assert "f1-audio" not in owner_entries
    assert (
        owner_entries["f1-power-profile"].disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )
    assert owner_entries["f1-battery"].disabled_by is er.RegistryEntryDisabler.USER
    assert owner_entries["f1-battery"].entity_id == battery.entity_id

    await _refresh(hass, coordinator, feeders, _feeder(supportsAudio=True))
    assert "f1-audio" in _entries(registry, entry)
    all_ids = {key: item.entity_id for key, item in _entries(registry, entry).items()}
    await _refresh(hass, coordinator, feeders, None)
    assert coordinator.feeders == {}
    assert hass.states.get(state_id).state == STATE_UNAVAILABLE
    assert devices.async_get(device_id) is not None
    assert {
        key: item.entity_id for key, item in _entries(registry, entry).items()
    } == all_ids

    await _refresh(hass, coordinator, feeders, _feeder())
    assert hass.states.get(state_id).state == "online"
    assert {
        key: item.entity_id for key, item in _entries(registry, entry).items()
    } == all_ids
    assert all(
        _entries(registry, entry)[key].entity_id == value
        for key, value in existing_ids.items()
    )
    assert _entries(registry, entry)["f1-state"].device_id == device_id


async def test_failed_refresh_changes_availability_without_losing_identity(
    hass, account
):
    entry, coordinator, feeders, refresh = account
    registry = er.async_get(hass)
    await _refresh(hass, coordinator, feeders, _feeder())
    entries = _entries(registry, entry)
    state_id = entries["f1-state"].entity_id
    before = {key: value.entity_id for key, value in entries.items()}
    refresh.side_effect = TimeoutError("fixture failure")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not coordinator.last_update_success
    assert hass.states.get(state_id).state == STATE_UNAVAILABLE
    assert {
        key: value.entity_id for key, value in _entries(registry, entry).items()
    } == before
    refresh.side_effect = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success
    assert hass.states.get(state_id).state == "online"


async def test_existing_controls_follow_role_and_capability_without_registry_changes(
    hass, account
):
    entry, coordinator, feeders, _ = account
    registry = er.async_get(hass)
    await _refresh(hass, coordinator, feeders, _feeder())
    entries = _entries(registry, entry)
    audio_id = entries["f1-audio"].entity_id
    offgrid_id = entries["f1-offgrid"].entity_id
    await _refresh(hass, coordinator, feeders, _feeder("FeederForMember"))
    assert hass.states.get(audio_id).state == STATE_UNAVAILABLE
    assert hass.states.get(offgrid_id).state == STATE_UNAVAILABLE
    await _refresh(hass, coordinator, feeders, _feeder(supportsAudio=False))
    assert hass.states.get(audio_id).state == STATE_UNAVAILABLE
    assert hass.states.get(offgrid_id).state != STATE_UNAVAILABLE
    assert _entries(registry, entry)["f1-audio"].entity_id == audio_id


async def test_confirmed_control_updates_other_entities_through_real_ha_service(
    hass, account
):
    entry, coordinator, feeders, _ = account
    registry = er.async_get(hass)
    await _refresh(hass, coordinator, feeders, _feeder())
    entries = _entries(registry, entry)
    offgrid_id = entries["f1-offgrid"].entity_id
    state_id = entries["f1-state"].entity_id
    assert hass.states.get(offgrid_id).state == "off"
    assert hass.states.get(state_id).state == "online"

    async def control(feeder, enabled):
        assert feeder.id == "f1" and enabled is True
        snapshot = _feeder(offGrid=True, state="OFF_GRID")
        feeders["f1"] = snapshot
        return Feeder(snapshot)

    with patch.object(
        coordinator.client, "toggle_off_grid", AsyncMock(side_effect=control)
    ) as method:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": offgrid_id}, blocking=True
        )
    await hass.async_block_till_done()
    method.assert_awaited_once()
    assert hass.states.get(offgrid_id).state == "on"
    assert hass.states.get(state_id).state == "off_grid"


async def test_reload_preserves_registry_ids_and_replaces_discovery_listeners(
    hass, account
):
    entry, old, feeders, _ = account
    registry = er.async_get(hass)
    await _refresh(hass, old, feeders, _feeder())
    before = {key: value.entity_id for key, value in _entries(registry, entry).items()}
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    current = hass.data[DOMAIN][entry.entry_id]
    assert current is not old
    assert not old._listeners
    assert current._listeners
    assert {
        key: value.entity_id for key, value in _entries(registry, entry).items()
    } == before
    assert hass.states.get(before["f1-state"]).state == "online"


async def test_current_runtime_object_controls_availability_before_listener_runs(hass):
    original = BirdBuddyDevice(_feeder())
    coordinator = MagicMock()
    coordinator.feeders = {"f1": original}
    coordinator.last_update_success = True
    sensor = BirdBuddyStateEntity(original, coordinator)
    audio = BirdBuddyAudioSwitch(original, coordinator)
    offgrid = BirdBuddyOffGridSwitch(original, coordinator)
    selector = BirdBuddyPowerProfileSelector(original, coordinator)
    assert (
        sensor.available
        and audio.available
        and offgrid.available
        and selector.available
    )
    current = BirdBuddyDevice(_feeder("FeederForMemberPending"))
    coordinator.feeders["f1"] = current
    assert audio.feeder is current
    assert not any(item.available for item in (sensor, audio, offgrid, selector))
    assert audio.is_on is None and offgrid.is_on is None
    assert selector.current_option is None
    coordinator.feeders.clear()
    assert not sensor.available


def test_pending_device_info_reads_only_identity_fields():
    feeder = BirdBuddyDevice(_feeder("FeederForMemberPending"))
    with patch.object(
        BirdBuddyDevice,
        "housing_type",
        new_callable=PropertyMock,
        side_effect=AssertionError("pending device queried private metadata"),
    ):
        info = feeder.device_info
    assert info.get("identifiers") == {(DOMAIN, "f1")}
    assert info.get("name") == "Fixture feeder"
    assert "sw_version" not in info
