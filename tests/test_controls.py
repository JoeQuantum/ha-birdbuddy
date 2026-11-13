"""Controls publish confirmed state and preserve failures without retrying writes."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

from birdbuddy.exceptions import AuthenticationFailedError
from birdbuddy.feeder import (
    Feeder,
    FeederControlError,
    FeederControlUnavailableError,
    PowerProfile,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
import pytest

from custom_components.birdbuddy.device import BirdBuddyDevice
from custom_components.birdbuddy.select import BirdBuddyPowerProfileSelector
from custom_components.birdbuddy.switch import (
    BirdBuddyAudioSwitch,
    BirdBuddyOffGridSwitch,
)


def _entities(hass):
    feeder = BirdBuddyDevice(
        {
            "id": "feeder",
            "name": "Fixture",
            "__typename": "FeederForOwner",
            "offGrid": False,
            "audioEnabled": False,
            "supportsAudio": True,
            "powerProfile": "STANDARD_MODE",
            "battery": {"percentage": 80},
        }
    )
    coordinator = MagicMock()
    coordinator.feeders = {feeder.id: feeder}
    coordinator.last_update_success = True
    coordinator.client.toggle_off_grid = AsyncMock()
    coordinator.client.toggle_audio_enabled = AsyncMock()
    coordinator.client.set_power_profile = AsyncMock()
    entities = (
        BirdBuddyOffGridSwitch(feeder, coordinator),
        BirdBuddyAudioSwitch(feeder, coordinator),
        BirdBuddyPowerProfileSelector(feeder, coordinator),
    )
    for entity in entities:
        entity.hass = hass
    return entities, coordinator


@pytest.mark.parametrize("audio", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_switch_publishes_confirmed_snapshot_to_all_consumers(
    hass, audio, enabled
):
    entities, coordinator = _entities(hass)
    entity = entities[1] if audio else entities[0]
    client_method = (
        coordinator.client.toggle_audio_enabled
        if audio
        else coordinator.client.toggle_off_grid
    )
    field = "audioEnabled" if audio else "offGrid"
    entity.feeder[field] = not enabled
    initial = deepcopy(dict(entity.feeder))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def confirmed(feeder, value):
        entered.set()
        await release.wait()
        return Feeder({**initial, field: value, "battery": {"percentage": 78}})

    client_method.side_effect = confirmed
    original = entity.feeder
    action = asyncio.create_task(
        entity.async_turn_on() if enabled else entity.async_turn_off()
    )
    await entered.wait()
    assert dict(original) == initial
    coordinator.async_update_listeners.assert_not_called()
    release.set()
    await action
    client_method.assert_awaited_once_with(original, enabled)
    assert entity.feeder is original
    assert entity.is_on is enabled
    assert entities[0].feeder.battery.percentage == 78
    coordinator.async_update_listeners.assert_called_once_with()


async def test_select_uses_configured_profile_and_publishes_observed_result(hass):
    entities, coordinator = _entities(hass)
    selector = entities[2]
    selector.feeder["smartEnergy"] = {
        "__typename": "FeederSmartEnergyEnabled",
        "runningPowerProfile": "POWER_SAVER_MODE",
    }
    assert selector.current_option == "standard_mode"
    coordinator.client.set_power_profile.return_value = {
        "powerProfile": "POWER_SAVER_MODE"
    }
    coordinator.client.feeders = {
        selector.feeder.id: Feeder(
            {
                **selector.feeder,
                "powerProfile": "POWER_SAVER_MODE",
                "battery": {"percentage": 77},
            }
        )
    }
    await selector.async_select_option("power_saver_mode")
    coordinator.client.set_power_profile.assert_awaited_once_with(
        selector.feeder, PowerProfile.POWER_SAVE
    )
    assert selector.current_option == "power_saver_mode"
    assert selector.feeder.battery.percentage == 77
    coordinator.async_update_listeners.assert_called_once_with()


async def test_completed_selection_without_confirmed_feeder_does_not_echo_value(hass):
    entities, coordinator = _entities(hass)
    coordinator.client.set_power_profile.return_value = {
        "powerProfile": "POWER_SAVER_MODE"
    }
    coordinator.client.feeders = {}
    with pytest.raises(HomeAssistantError, match="action completed"):
        await entities[2].async_select_option("power_saver_mode")
    assert entities[2].current_option == "standard_mode"
    coordinator.async_update_listeners.assert_not_called()


@pytest.mark.parametrize("value", [None, "FUTURE_PROFILE", "UNKNOWN"])
async def test_unknown_selection_is_unavailable_without_using_running_mode(
    hass, value, caplog
):
    entities, coordinator = _entities(hass)
    selector = entities[2]
    selector.feeder["powerProfile"] = value
    selector.feeder["smartEnergy"] = {
        "__typename": "FeederSmartEnergyEnabled",
        "runningPowerProfile": "STANDARD_MODE",
    }
    assert selector.current_option is None
    assert not selector.available
    with pytest.raises(ServiceValidationError, match="unavailable"):
        await selector.async_select_option("standard_mode")
    coordinator.client.set_power_profile.assert_not_awaited()
    assert "FUTURE_PROFILE" not in caplog.text


@pytest.mark.parametrize(
    "value", ["UNKNOWN", "unknown", "invented", "STANDARD_MODE", None]
)
async def test_invalid_selection_fails_before_client_call(hass, value):
    entities, coordinator = _entities(hass)
    with pytest.raises(ServiceValidationError, match="Unsupported"):
        await entities[2].async_select_option(value)
    coordinator.client.set_power_profile.assert_not_awaited()


@pytest.mark.parametrize(
    "condition", ["unhealthy", "removed", "member", "pending", "no_audio"]
)
async def test_unavailable_control_never_reaches_sdk(hass, condition):
    entities, coordinator = _entities(hass)
    if condition == "unhealthy":
        coordinator.last_update_success = False
    elif condition == "removed":
        coordinator.feeders.clear()
    elif condition == "no_audio":
        entities[0].feeder["supportsAudio"] = False
        entities = (entities[1],)
    else:
        entities[0].feeder["__typename"] = (
            "FeederForMember" if condition == "member" else "FeederForMemberPending"
        )
    for entity in entities:
        with pytest.raises(ServiceValidationError, match="unavailable"):
            if isinstance(entity, BirdBuddyPowerProfileSelector):
                await entity.async_select_option("standard_mode")
            else:
                await entity.async_turn_on()
    coordinator.client.toggle_off_grid.assert_not_awaited()
    coordinator.client.toggle_audio_enabled.assert_not_awaited()
    coordinator.client.set_power_profile.assert_not_awaited()


@pytest.mark.parametrize(
    ("completed", "message"),
    [
        (True, "action completed"),
        (False, "did not complete"),
        (None, "completion is unknown"),
    ],
)
async def test_completion_failure_is_honest_and_does_not_retry_or_change_state(
    hass, caplog, completed, message
):
    entities, coordinator = _entities(hass)
    entity = entities[0]
    before = deepcopy(dict(entity.feeder))
    coordinator.client.toggle_off_grid.side_effect = FeederControlError(
        "https://private.invalid/?Signature=SECRET", action_completed=completed
    )
    with pytest.raises(HomeAssistantError, match=message) as raised:
        await entity.async_turn_on()
    assert dict(entity.feeder) == before
    assert coordinator.client.toggle_off_grid.await_count == 1
    coordinator.async_update_listeners.assert_not_called()
    assert "SECRET" not in str(raised.value) + caplog.text
    assert raised.value.__suppress_context__


@pytest.mark.parametrize("nested", [False, True])
async def test_auth_failure_starts_reauth_without_leaking_details(hass, caplog, nested):
    entities, coordinator = _entities(hass)
    auth = AuthenticationFailedError("PRIVATE_ACCOUNT_DETAIL")
    if nested:
        error = FeederControlError("PRIVATE_ACCOUNT_DETAIL", action_completed=True)
        error.__cause__ = auth
    else:
        error = auth
    coordinator.client.toggle_audio_enabled.side_effect = error
    with pytest.raises(HomeAssistantError, match="sign in again") as raised:
        await entities[1].async_turn_on()
    if nested:
        assert "action completed" in str(raised.value)
    coordinator.config_entry.async_start_reauth.assert_called_once_with(hass)
    assert isinstance(
        coordinator.async_set_update_error.call_args.args[0], ConfigEntryAuthFailed
    )
    coordinator.async_update_listeners.assert_not_called()
    assert "PRIVATE_ACCOUNT_DETAIL" not in str(raised.value) + caplog.text


@pytest.mark.parametrize(
    ("error", "expected", "message"),
    [
        (
            FeederControlUnavailableError("SECRET"),
            ServiceValidationError,
            "role or device capability",
        ),
        (ValueError("SECRET"), ServiceValidationError, "Unsupported"),
        (RuntimeError("SECRET"), HomeAssistantError, "could not be confirmed"),
    ],
)
async def test_other_failures_are_safe_and_do_not_publish(
    hass, caplog, error, expected, message
):
    entities, coordinator = _entities(hass)
    coordinator.client.toggle_off_grid.side_effect = error
    with pytest.raises(expected, match=message) as raised:
        await entities[0].async_turn_on()
    coordinator.async_update_listeners.assert_not_called()
    assert "SECRET" not in str(raised.value) + caplog.text


async def test_cancellation_reaches_client_and_does_not_publish(hass):
    entities, coordinator = _entities(hass)
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def operation(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    coordinator.client.toggle_off_grid.side_effect = operation
    action = asyncio.create_task(entities[0].async_turn_on())
    await entered.wait()
    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action
    assert cancelled.is_set()
    assert entities[0].is_on is False
    coordinator.async_update_listeners.assert_not_called()


@pytest.mark.parametrize("result", [None, {"id": "feeder"}, Feeder({"id": "another"})])
async def test_malformed_result_does_not_overwrite_current_feeder(hass, result):
    entities, coordinator = _entities(hass)
    coordinator.client.toggle_off_grid.return_value = result
    with pytest.raises(HomeAssistantError, match="unrecognized result"):
        await entities[0].async_turn_on()
    assert entities[0].is_on is False
    coordinator.async_update_listeners.assert_not_called()


async def test_removal_during_action_does_not_resurrect_runtime_feeder(hass):
    entities, coordinator = _entities(hass)
    entity = entities[0]

    async def operation(*args):
        result = Feeder({**entity.feeder, "offGrid": True})
        coordinator.feeders.clear()
        return result

    coordinator.client.toggle_off_grid.side_effect = operation
    with pytest.raises(HomeAssistantError, match="membership changed"):
        await entity.async_turn_on()
    assert coordinator.feeders == {}
    assert not entity.available
    coordinator.async_update_listeners.assert_not_called()
