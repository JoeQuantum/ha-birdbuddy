"""Test component setup."""
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)

from custom_components.birdbuddy import _async_migrate_enabled_defaults
from custom_components.birdbuddy.const import DOMAIN


@pytest.fixture(name="expected_lingering_timers")
def expected_lingering_timers_fixture():
    """Fixture to set expected_lingering_timers."""
    return True


async def test_async_setup(hass):
    """Test the component gets setup."""
    assert await async_setup_component(hass, DOMAIN, {}) is True


async def test_setup_entry(hass: HomeAssistant):
    config = {
        "email": "test@email.com",
        "password": "test-password",
    }
    config_entry = MockConfigEntry(domain="birdbuddy", data=config, state=ConfigEntryState.NOT_LOADED)
    config_entry.add_to_hass(hass)

    with patch(
        "birdbuddy.client.BirdBuddy.refresh",
        return_value=True,
    ), patch(
        "birdbuddy.client.BirdBuddy.refresh_feed",
        return_value=[],
    ), patch(
        # Any code path that reaches the live feed during setup (e.g. the
        # new-feed-item event seeding) must be mocked here — an unmocked
        # `feed()` performs real DNS/HTTP, which leaks a resolver thread the
        # harness teardown fails on. That leak only manifests under aiodns, so
        # it slips past a local venv without aiodns and only turns CI red. Mock
        # it so the setup path can never reach the network.
        "birdbuddy.client.BirdBuddy.feed",
        new=AsyncMock(return_value=MagicMock(filter=MagicMock(return_value=[]))),
    ), patch(
        "birdbuddy.client.BirdBuddy.refresh_collections",
        new=AsyncMock(return_value={}),
    ), patch(
        "birdbuddy.client.BirdBuddy.feeders",
        new_callable=PropertyMock,
        return_value={"feeder1": {"id": "feeder1", "name": "Test Feeder"}}
    ), patch(
        # RecentVisitors schedules `_update_latest_visitor` via `hass.add_job`
        # when the first listener registers. That method walks into multiple
        # unmocked BirdBuddy methods (feed, refresh_collections, …) and hits
        # the network. This test asserts setup succeeds; the visitor refresh
        # path is exercised in dedicated tests. Stub it out here so we don't
        # need to mock every method it touches.
        "custom_components.birdbuddy.visitors.RecentVisitors._update_latest_visitor",
        new=AsyncMock(return_value=None),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        # Tear down the entry inside the patch block so the coordinator's
        # periodic refresh timer can't fire against real aiohttp/aiodns after
        # the mocks release.
        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_entry_no_feeders(hass: HomeAssistant):
    config = {
        "email": "test@email.com",
        "password": "test-password",
    }
    config_entry = MockConfigEntry(domain="birdbuddy", data=config, state=ConfigEntryState.NOT_LOADED)
    config_entry.add_to_hass(hass)

    with patch(
        "birdbuddy.client.BirdBuddy.refresh",
        return_value=True,
    ), patch(
        "birdbuddy.client.BirdBuddy.refresh_feed",
        return_value=[],
    ):
        # Raises UpdateFailed -> return False
        assert not await hass.config_entries.async_setup(config_entry.entry_id)


async def test_setup_entry_refresh_fails(hass: HomeAssistant):
    config = {
        "email": "test@email.com",
        "password": "test-password",
    }
    config_entry = MockConfigEntry(domain="birdbuddy", data=config, state=ConfigEntryState.NOT_LOADED)
    config_entry.add_to_hass(hass)

    with patch(
        "birdbuddy.client.BirdBuddy.refresh",
        side_effect=Exception,
    ):
        # Raises UpdateFailed -> return False
        assert not await hass.config_entries.async_setup(config_entry.entry_id)


async def test_migration_re_enables_promoted_default_entities(hass: HomeAssistant):
    """Regression for the v0.1.0 → v0.1.1 enable-by-default upgrade.

    HA persists `disabled_by` per entity in its registry, so changing
    `_attr_entity_registry_enabled_default = True` in code does NOT
    retroactively enable already-registered entities — users coming from
    older versions would have to manually toggle each one on. The
    `_async_migrate_enabled_defaults` helper does it for them at integration
    load by clearing `disabled_by=INTEGRATION` for unique_id suffixes in
    `_PROMOTED_DISABLED_DEFAULT_SUFFIXES`.

    Asserts three things at once:
      - matching suffix + integration-disabled → re-enabled
      - non-matching suffix + integration-disabled → stays disabled
        (otherwise we'd accidentally re-enable things like the Signal
        entity that's legitimately disabled-by-default)
      - matching suffix + user-disabled → stays disabled (respect user choice)
    """
    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)
    registry = er.async_get(hass)

    promoted = registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="feeder-a-recent-visitor",
        config_entry=config_entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    unaffected = registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="feeder-a-signal",
        config_entry=config_entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    user_disabled = registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id="feeder-b-recent-visitor",
        config_entry=config_entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )

    _async_migrate_enabled_defaults(hass, config_entry)

    assert registry.async_get(promoted.entity_id).disabled_by is None
    assert (
        registry.async_get(unaffected.entity_id).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )
    assert (
        registry.async_get(user_disabled.entity_id).disabled_by
        is er.RegistryEntryDisabler.USER
    )
