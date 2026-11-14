"""The tests for Bird Buddy device triggers."""

from types import SimpleNamespace

import pytest
from pytest_unordered import unordered
from homeassistant.components.automation.const import DOMAIN as AUTOMATION_DOMAIN
from homeassistant.components.device_automation import (
    DeviceAutomationType,
)
from homeassistant.components.device_automation.exceptions import (
    InvalidDeviceAutomationConfig,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import EventOrigin
from homeassistant.helpers import device_registry
from homeassistant.setup import async_setup_component
from tests.ha_compat import common as ha_test_common

from custom_components.birdbuddy import DOMAIN
from custom_components.birdbuddy import device_trigger
from custom_components.birdbuddy.const import EVENT_NEW_POSTCARD


async def setup_automation(hass, device_id, feeder_id, trigger_type):
    """Set up an automation trigger for testing triggering."""
    return await async_setup_component(
        hass,
        AUTOMATION_DOMAIN,
        {
            AUTOMATION_DOMAIN: [
                {
                    "trigger": {
                        "platform": "device",
                        "domain": DOMAIN,
                        "device_id": device_id,
                        "feeder_id": feeder_id,
                        "type": trigger_type,
                    },
                    "action": {
                        "service": "test.automation",
                        "data": {"message": "triggered"},
                    },
                },
            ]
        },
    )


async def test_get_triggers(
    hass, device_reg: device_registry.DeviceRegistry, entity_reg
):
    """Test we get the expected triggers from a birdbuddy."""
    config_entry = ha_test_common.MockConfigEntry(domain="birdbuddy", data={})
    config_entry.add_to_hass(hass)
    device_entry = device_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "feeder1")},
    )
    expected_triggers = [
        {
            "platform": "device",
            "domain": DOMAIN,
            "type": "new_postcard",
            "device_id": device_entry.id,
            "feeder_id": "feeder1",
            # We didn't add this, but the test produces it
            "metadata": {},
        },
    ]
    triggers = await ha_test_common.async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, device_entry.id
    )
    assert triggers == unordered(expected_triggers)


@pytest.mark.parametrize(
    ("event_feeder", "event_account", "source", "expected"),
    [
        ("feeder1", "same", "live", 1),
        ("feeder2", "same", "live", 0),
        ("feeder1", "different", "live", 0),
        ("feeder1", "same", "backfill", 0),
    ],
)
async def test_account_scoped_device_trigger(
    hass, device_reg, calls, event_feeder, event_account, source, expected
):
    entry = ha_test_common.MockConfigEntry(
        domain=DOMAIN, data={}, state=ConfigEntryState.LOADED
    )
    entry.add_to_hass(hass)
    device = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "feeder1")}
    )
    hass.data[DOMAIN] = {
        entry.entry_id: SimpleNamespace(
            config_entry=entry, feeders={"feeder1": object()}
        )
    }
    assert await setup_automation(hass, device.id, "feeder1", "new_postcard")
    hass.bus.async_fire(
        EVENT_NEW_POSTCARD,
        {
            "feeder_id": event_feeder,
            "config_entry_id": entry.entry_id
            if event_account == "same"
            else "other-entry",
            "source": source,
            "postcard_id": "postcard-a",
        },
        origin=EventOrigin.remote,
    )
    await hass.async_block_till_done()
    assert len(calls) == expected


async def test_config_schema(hass, device_reg):
    """Test we get the expected triggers from a birdbuddy."""
    config_entry = ha_test_common.MockConfigEntry(
        domain="birdbuddy", data={}, state=ConfigEntryState.LOADED
    )
    config_entry.add_to_hass(hass)
    device_entry = device_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "feeder1")},
    )
    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = object()
    # Test that invalid config with no device coordinator raises
    config = {
        "platform": "device",
        "domain": "birdbuddy",
        "device_id": device_entry.id,
        "type": "new_postcard",
    }
    await device_trigger.async_validate_trigger_config(hass, config)


async def test_config_schema_no_coordinator(hass, device_reg):
    """Test we get the expected triggers from a birdbuddy."""
    config_entry = ha_test_common.MockConfigEntry(
        domain="birdbuddy", data={}, state=ConfigEntryState.LOADED
    )
    config_entry.add_to_hass(hass)
    device_entry = device_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "feeder1")},
    )
    hass.data.setdefault(DOMAIN, {})[config_entry.entry_id] = None
    # Test that invalid config with no device coordinator raises
    config = {
        "platform": "device",
        "domain": "birdbuddy",
        "feeder_id": "feeder1",
        "device_id": device_entry.id,
        "type": "new_postcard",
    }
    with pytest.raises(InvalidDeviceAutomationConfig):
        await device_trigger.async_validate_trigger_config(hass, config)
