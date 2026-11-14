"""Firmware monitoring releases local progress after every terminal path."""

import asyncio
import traceback
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from birdbuddy.exceptions import (
    AuthenticationFailedError,
    GraphqlError,
    NoFirmwareUpdateAvailableError,
    NoResponseError,
)
from birdbuddy.feeder import (
    FeederControlError,
    FeederUpdateStatus,
    FirmwareUpdatePreconditionError,
    FirmwareUpdateUnknownStateError,
)
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.birdbuddy import update as updates
from custom_components.birdbuddy.device import BirdBuddyDevice
from custom_components.birdbuddy.update import BirdBuddyUpdate


def _status(kind="Progress", progress=42):
    data: dict[str, Any] = {"__typename": f"FeederFirmwareUpdate{kind}Result"}
    if kind == "Progress":
        data["progress"] = progress
    if kind == "Succeeded":
        data["feeder"] = {"firmwareVersion": "2.0", "availableFirmwareVersion": None}
    if kind == "Failed":
        data["failedReason"] = "BATTERY_LOW"
    return FeederUpdateStatus(data)


def _entity(hass):
    feeder = BirdBuddyDevice(
        {
            "id": "feeder",
            "name": "Fixture",
            "__typename": "FeederForOwner",
            "firmwareVersion": "1.0",
            "availableFirmwareVersion": "2.0",
        }
    )
    coordinator = MagicMock()
    coordinator.feeders = {feeder.id: feeder}
    coordinator.last_update_success = True
    coordinator.client.update_firmware_start = AsyncMock(return_value=_status())
    coordinator.client.update_firmware_check = AsyncMock(
        return_value=_status("Succeeded")
    )
    coordinator.async_request_refresh = AsyncMock()
    entity = BirdBuddyUpdate(feeder, coordinator)
    entity.hass = hass
    cast(Any, entity).async_write_ha_state = MagicMock()
    return entity, coordinator


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr(updates, "POLL_INTERVAL", 0)


async def test_four_errors_clear_42_percent_and_later_attempt_can_resume(hass, caplog):
    entity, coordinator = _entity(hass)
    error = GraphqlError(
        {
            "message": "PRIVATE_ACCOUNT_DETAIL",
            "extensions": {"code": "INTERNAL_SERVER_ERROR"},
        }
    )
    coordinator.client.update_firmware_check.side_effect = [error] * 4
    with pytest.raises(HomeAssistantError, match="lost contact") as caught:
        await entity.async_install(None, False)
    assert entity.in_progress is False
    assert entity.extra_state_attributes["firmware_server_status"] == "in_progress"
    assert entity.extra_state_attributes["firmware_server_progress"] == 42
    assert "PRIVATE_ACCOUNT_DETAIL" not in caplog.text
    assert "PRIVATE_ACCOUNT_DETAIL" not in "".join(
        traceback.format_exception(caught.value)
    )
    assert "(1/4)" in caplog.text
    coordinator.client.update_firmware_check.side_effect = [_status("Succeeded")]
    await entity.async_install(None, False)
    assert entity.in_progress is False
    assert entity.installed_version == "2.0"
    assert entity.extra_state_attributes["firmware_server_status"] == "succeeded"
    assert coordinator.client.update_firmware_start.await_count == 2
    coordinator.client.update_firmware_start.assert_awaited_with(
        entity.feeder, immediate=True, expected_version=None
    )


@pytest.mark.parametrize(
    "status",
    [_status("Failed"), FeederUpdateStatus({"__typename": "FutureFirmwareResult"})],
)
async def test_failed_and_unknown_results_stop_monitoring(hass, status):
    entity, coordinator = _entity(hass)
    coordinator.client.update_firmware_start.return_value = status
    with pytest.raises(HomeAssistantError, match=r"failure|unknown"):
        await entity.async_install(None, False)
    assert entity.in_progress is False
    assert entity.extra_state_attributes["firmware_server_status"] in {
        "failed",
        "unknown",
    }
    coordinator.client.update_firmware_check.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [
        NoFirmwareUpdateAvailableError(),
        FirmwareUpdatePreconditionError("not ready", action_completed=False),
    ],
)
async def test_start_preconditions_leave_no_local_busy_state(hass, error):
    entity, coordinator = _entity(hass)
    coordinator.client.update_firmware_start.side_effect = error
    with pytest.raises(HomeAssistantError, match=r"available|preconditions"):
        await entity.async_install(None, False)
    assert entity.in_progress is False
    coordinator.client.update_firmware_check.assert_not_awaited()


async def test_concurrent_installs_are_rejected(hass):
    entity, coordinator = _entity(hass)
    entered = asyncio.Event()

    async def start(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    coordinator.client.update_firmware_start.side_effect = start
    first = asyncio.create_task(entity.async_install(None, False))
    await entered.wait()
    assert entity.in_progress is True
    with pytest.raises(HomeAssistantError, match="already active"):
        await entity.async_install(None, False)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert entity.in_progress is False
    assert entity._install_task is None
    assert coordinator.client.update_firmware_start.await_count == 1


async def test_unload_cancels_and_awaits_owned_monitor(hass):
    entity, coordinator = _entity(hass)
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def check(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    coordinator.client.update_firmware_check.side_effect = check
    installing = asyncio.create_task(entity.async_install(None, False))
    await entered.wait()
    with patch(
        "custom_components.birdbuddy.entity.BirdBuddyMixin.async_will_remove_from_hass",
        new=AsyncMock(),
    ):
        await entity.async_will_remove_from_hass()
    with pytest.raises(asyncio.CancelledError):
        await installing
    assert cancelled.is_set()
    assert entity.in_progress is False
    assert entity._install_task is None
    assert entity.extra_state_attributes["firmware_server_progress"] == 42


async def test_monitor_deadline_preserves_server_progress(hass, monkeypatch):
    entity, coordinator = _entity(hass)
    monkeypatch.setattr(updates, "MONITOR_TIMEOUT", 0.01)

    async def check(*args):
        await asyncio.Event().wait()

    coordinator.client.update_firmware_check.side_effect = check
    with pytest.raises(HomeAssistantError, match="timed out"):
        await entity.async_install(None, False)
    assert entity.in_progress is False
    assert entity.extra_state_attributes["firmware_server_progress"] == 42


async def test_error_budget_resets_after_successful_progress(hass):
    entity, coordinator = _entity(hass)
    coordinator.client.update_firmware_check.side_effect = (
        [NoResponseError()] * 3
        + [_status("Progress", 70)]
        + [NoResponseError()] * 3
        + [_status("Succeeded")]
    )
    await entity.async_install(None, False)
    assert entity.in_progress is False
    assert coordinator.client.update_firmware_check.await_count == 8


async def test_successful_backend_state_survives_final_refresh_failure(hass):
    entity, coordinator = _entity(hass)
    coordinator.client.update_firmware_start.return_value = _status("Succeeded")
    coordinator.async_request_refresh.side_effect = RuntimeError(
        "PRIVATE_ACCOUNT_DETAIL"
    )
    with pytest.raises(HomeAssistantError, match="could not be confirmed"):
        await entity.async_install(None, False)
    assert entity.in_progress is False
    assert entity.installed_version == "2.0"
    assert entity.extra_state_attributes["firmware_server_status"] == "succeeded"


@pytest.mark.parametrize("role", ["FeederForMember", "FeederForMemberPending"])
async def test_nonowner_install_never_reaches_client(hass, role):
    entity, coordinator = _entity(hass)
    entity.feeder["__typename"] = role
    with pytest.raises(HomeAssistantError, match="owner"):
        await entity.async_install(None, False)
    coordinator.client.update_firmware_start.assert_not_awaited()


async def test_unknown_check_variant_is_terminal_and_preserved(hass):
    entity, coordinator = _entity(hass)
    unknown = FeederUpdateStatus({"__typename": "FutureFirmwareResult"})
    coordinator.client.update_firmware_check.side_effect = (
        FirmwareUpdateUnknownStateError("Unknown firmware update state", status=unknown)
    )
    with pytest.raises(HomeAssistantError, match="unknown firmware"):
        await entity.async_install(None, False)
    assert entity.in_progress is False
    assert entity.extra_state_attributes["firmware_server_status"] == "unknown"
    assert coordinator.client.update_firmware_check.await_count == 1


@pytest.mark.parametrize("phase", ["start", "check"])
@pytest.mark.parametrize("wrapped", [False, True])
async def test_firmware_auth_failure_starts_reauth_without_retry(hass, phase, wrapped):
    entity, coordinator = _entity(hass)
    private = "synthetic-private-auth-detail"
    error: Exception = AuthenticationFailedError(private)
    if wrapped:
        error = FeederControlError(private)
        error.__cause__ = AuthenticationFailedError(private)
    method = getattr(coordinator.client, "update_firmware_" + phase)
    method.side_effect = error
    with pytest.raises(HomeAssistantError, match="sign in again") as caught:
        await entity.async_install(None, False)
    coordinator.config_entry.async_start_reauth.assert_called_once_with(hass)
    coordinator.async_set_update_error.assert_called_once()
    method.assert_awaited_once()
    assert entity.in_progress is False
    assert entity._install_task is None
    assert private not in "".join(traceback.format_exception(caught.value))
    if phase == "check":
        assert entity.extra_state_attributes["firmware_server_status"] == "in_progress"
        assert entity.extra_state_attributes["firmware_server_progress"] == 42
