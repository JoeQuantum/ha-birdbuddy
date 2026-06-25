"""Tests for the Bird Buddy options flow (configurable polling interval)."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.birdbuddy.const import (
    CONF_POLLING_INTERVAL,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
)


def _entry(options: dict | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={"email": "test@email.com", "password": "pw"},
        options=options or {},
    )


async def test_options_flow_sets_and_persists_interval(hass: HomeAssistant) -> None:
    """Submitting a valid interval writes it to entry.options."""
    entry = _entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_POLLING_INTERVAL: 5}
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_POLLING_INTERVAL] == 5


async def test_options_flow_prefills_current_value(hass: HomeAssistant) -> None:
    """The form defaults to the currently-configured interval (or the default)."""
    entry = _entry(options={CONF_POLLING_INTERVAL: 15})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    # Required-with-default: validating an empty input yields the prefilled value.
    assert result["data_schema"]({})[CONF_POLLING_INTERVAL] == 15

    entry2 = _entry()
    entry2.add_to_hass(hass)
    result2 = await hass.config_entries.options.async_init(entry2.entry_id)
    assert (
        result2["data_schema"]({})[CONF_POLLING_INTERVAL]
        == DEFAULT_POLLING_INTERVAL
    )


@pytest.mark.parametrize("bad", [0, 21, 100])
async def test_options_flow_rejects_out_of_range(
    hass: HomeAssistant, bad: int
) -> None:
    """Values outside 1..20 are rejected and nothing is persisted."""
    entry = _entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={CONF_POLLING_INTERVAL: bad}
        )
    assert CONF_POLLING_INTERVAL not in entry.options
