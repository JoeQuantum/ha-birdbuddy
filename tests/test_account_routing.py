"""Account routing must finish validation before any client mutation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from birdbuddy.user import BirdBuddyUser
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

from custom_components.birdbuddy import _setup_services
from custom_components.birdbuddy.const import CONF_ACCOUNT_ID, DOMAIN
from custom_components.birdbuddy.hass_util import _find_coordinator_by_device
from tests.ha_compat import common as ha_test_common


def _account(hass, account_id, feeders, *, loaded=True):
    entry = ha_test_common.MockConfigEntry(
        domain=DOMAIN,
        unique_id=account_id + "@example.invalid",
        data={CONF_ACCOUNT_ID: account_id},
        state=ConfigEntryState.LOADED if loaded else ConfigEntryState.NOT_LOADED,
    )
    entry.add_to_hass(hass)
    coordinator = SimpleNamespace(
        feeders={fid: {} for fid in feeders},
        client=SimpleNamespace(user=BirdBuddyUser({"id": account_id})),
        handle_collect_postcard=AsyncMock(return_value=True),
    )
    if loaded:
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    return entry, coordinator


async def _collect(hass, feeder_id="shared", **targets):
    return await hass.services.async_call(
        DOMAIN,
        "collect_postcard",
        {
            "postcard": {"id": "postcard-a"},
            "sighting": {
                "feeder": {"id": feeder_id, "name": "Fixture feeder"},
                "sightingReport": {},
            },
            **targets,
        },
        blocking=True,
    )


async def test_migrated_composite_device_does_not_select_its_primary_account(hass):
    """Current HA can resolve old shared device IDs to a composite view."""
    entry_a, first = _account(hass, "account-a", ["shared"])
    entry_b, second = _account(hass, "account-b", ["shared"])
    legacy_device = SimpleNamespace(
        id="old-shared-device-id",
        identifiers={(DOMAIN, "shared")},
        is_composite_device=True,
        config_entry_id=entry_a.entry_id,
        config_entries={entry_a.entry_id, entry_b.entry_id},
    )
    _setup_services(hass)
    with patch.object(dr.async_get(hass), "async_get", return_value=legacy_device):
        with pytest.raises(ServiceValidationError, match="ambiguous"):
            await _collect(hass, device_id=legacy_device.id)
        first.handle_collect_postcard.assert_not_awaited()
        second.handle_collect_postcard.assert_not_awaited()
        await _collect(
            hass, device_id=legacy_device.id, config_entry_id=entry_b.entry_id
        )
    first.handle_collect_postcard.assert_not_awaited()
    second.handle_collect_postcard.assert_awaited_once()


@pytest.mark.parametrize("target", ["config_entry_id", "account_id"])
async def test_explicit_account_routes_shared_feeder(hass, target):
    _, first = _account(hass, "account-a", ["shared"])
    entry, second = _account(hass, "account-b", ["shared"])
    _setup_services(hass)
    await _collect(
        hass, **{target: entry.entry_id if target == "config_entry_id" else "account-b"}
    )
    first.handle_collect_postcard.assert_not_awaited()
    second.handle_collect_postcard.assert_awaited_once()


async def test_unique_feeder_can_resolve_without_an_explicit_account(hass):
    _, first = _account(hass, "account-a", ["feeder-a"])
    _, second = _account(hass, "account-b", ["feeder-b"])
    _setup_services(hass)
    await _collect(hass, feeder_id="feeder-b")
    first.handle_collect_postcard.assert_not_awaited()
    second.handle_collect_postcard.assert_awaited_once()


async def test_single_account_retains_historical_feeder_convenience(hass):
    _, coordinator = _account(hass, "account-a", ["repaired-feeder"])
    _setup_services(hass)
    await _collect(hass, feeder_id="old-feeder")
    coordinator.handle_collect_postcard.assert_awaited_once()


@pytest.mark.parametrize(
    "case",
    [
        "shared",
        "unknown_feeder",
        "conflicting_account",
        "conflicting_feeder",
        "missing_entry",
        "missing_account",
    ],
)
async def test_ambiguous_and_conflicting_targets_never_mutate(hass, case):
    first_entry, first = _account(hass, "account-a", ["shared", "feeder-a"])
    _, second = _account(hass, "account-b", ["shared", "feeder-b"])
    _setup_services(hass)
    targets = {}
    feeder = "shared"
    if case == "unknown_feeder":
        feeder = "unknown"
    elif case == "conflicting_account":
        targets = {"config_entry_id": first_entry.entry_id, "account_id": "account-b"}
    elif case == "conflicting_feeder":
        targets = {"config_entry_id": first_entry.entry_id}
        feeder = "feeder-b"
    elif case == "missing_entry":
        targets = {"config_entry_id": "absent"}
    elif case == "missing_account":
        targets = {"account_id": "absent"}
    with pytest.raises(ServiceValidationError):
        await _collect(hass, feeder_id=feeder, **targets)
    first.handle_collect_postcard.assert_not_awaited()
    second.handle_collect_postcard.assert_not_awaited()


async def test_shared_unloaded_account_is_not_silently_ignored(hass):
    entry_a, first = _account(hass, "account-a", ["shared"])
    entry_b, second = _account(hass, "account-b", ["shared"], loaded=False)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry_a.entry_id, identifiers={(DOMAIN, "shared")}
    )
    other_device = registry.async_get_or_create(
        config_entry_id=entry_b.entry_id, identifiers={(DOMAIN, "shared")}
    )
    _setup_services(hass)
    with pytest.raises(ServiceValidationError, match="ambiguous"):
        await _collect(hass)
    with pytest.raises(ServiceValidationError, match="not loaded"):
        await _collect(hass, config_entry_id=entry_b.entry_id)
    if device.id == other_device.id:
        # Older HA shares one registry device across accounts.
        with pytest.raises(ValueError, match="ambiguous"):
            _find_coordinator_by_device(hass, device.id)
    else:
        # Current HA's distinct device IDs explicitly identify their account.
        assert _find_coordinator_by_device(hass, device.id) is first
        with pytest.raises(ValueError, match="not loaded"):
            _find_coordinator_by_device(hass, other_device.id)
    first.handle_collect_postcard.assert_not_awaited()
    second.handle_collect_postcard.assert_not_awaited()
    await _collect(hass, config_entry_id=entry_a.entry_id)
    first.handle_collect_postcard.assert_awaited_once()


async def test_device_and_payload_feeder_must_agree(hass):
    entry, coordinator = _account(hass, "account-a", ["feeder-a", "feeder-b"])
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "feeder-a")}
    )
    _setup_services(hass)
    with pytest.raises(ServiceValidationError, match="do not match"):
        await _collect(hass, feeder_id="feeder-b", device_id=device.id)
    coordinator.handle_collect_postcard.assert_not_awaited()
    await _collect(hass, feeder_id="feeder-a", device_id=device.id)
    coordinator.handle_collect_postcard.assert_awaited_once()


async def test_no_accounts_and_missing_devices_are_actionable_errors(hass):
    _setup_services(hass)
    with pytest.raises(ServiceValidationError, match="No Bird Buddy account"):
        await _collect(hass)
    _, coordinator = _account(hass, "account-a", ["shared"])
    with pytest.raises(ServiceValidationError, match="device was not found"):
        await _collect(hass, device_id="absent")
    coordinator.handle_collect_postcard.assert_not_awaited()
