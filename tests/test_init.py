"""Test component setup."""

from unittest.mock import AsyncMock, patch, PropertyMock

import pytest
from birdbuddy.user import BirdBuddyUser
from birdbuddy.feed import Feed
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from tests.ha_compat import common as ha_test_common

from custom_components.birdbuddy import _async_migrate_enabled_defaults
from custom_components.birdbuddy.const import DOMAIN


@pytest.fixture(name="expected_lingering_timers")
def expected_lingering_timers_fixture():
    """Fixture to set expected_lingering_timers."""
    return True


async def test_async_setup(hass):
    """Test the component gets setup."""
    assert await async_setup_component(hass, DOMAIN, {}) is True


@pytest.fixture
def hass_storage():
    """Prove setup can persist intake through Home Assistant's real writer."""
    yield {}


@pytest.fixture
def hass_config_dir(tmp_path):
    return str(tmp_path)


@pytest.mark.parametrize("feeder_count", [0, 1])
async def test_setup_entry(hass: HomeAssistant, feeder_count):
    config_entry = ha_test_common.MockConfigEntry(
        domain=DOMAIN,
        data={"email": "test@email.com", "password": "test-password"},
        state=ConfigEntryState.NOT_LOADED,
    )
    config_entry.add_to_hass(hass)
    with (
        patch("birdbuddy.client.BirdBuddy.refresh", return_value=True),
        patch(
            "birdbuddy.client.BirdBuddy.user",
            new_callable=PropertyMock,
            return_value=BirdBuddyUser(
                {"id": "account-test-id", "email": "test@email.com"}
            ),
        ),
        patch(
            "birdbuddy.client.BirdBuddy.feed",
            new=AsyncMock(
                return_value=Feed(
                    {
                        "edges": [],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                )
            ),
        ),
        patch(
            "birdbuddy.client.BirdBuddy.feeders",
            new_callable=PropertyMock,
            return_value={
                "feeder1": {
                    "id": "feeder1",
                    "name": "Test Feeder",
                    "__typename": "FeederForMember",
                }
            }
            if feeder_count
            else {},
        ),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        coordinator = hass.data[DOMAIN][config_entry.entry_id]
        assert coordinator.account_id == "account-test-id"
        assert coordinator.intake.is_bootstrapped
        assert coordinator.feed_error_type is None
        assert len(coordinator.feeders) == feeder_count
        assert await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_entry_refresh_fails(hass: HomeAssistant):
    config = {
        "email": "test@email.com",
        "password": "test-password",
    }
    config_entry = ha_test_common.MockConfigEntry(
        domain="birdbuddy", data=config, state=ConfigEntryState.NOT_LOADED
    )
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
    config_entry = ha_test_common.MockConfigEntry(domain=DOMAIN, data={})
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

    promoted_after = registry.async_get(promoted.entity_id)
    assert promoted_after is not None
    assert promoted_after.disabled_by is None
    unaffected_after = registry.async_get(unaffected.entity_id)
    assert unaffected_after is not None
    assert unaffected_after.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    user_disabled_after = registry.async_get(user_disabled.entity_id)
    assert user_disabled_after is not None
    assert user_disabled_after.disabled_by is er.RegistryEntryDisabler.USER
