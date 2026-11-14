"""Bounded Bird Buddy firmware installation and monitoring."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from birdbuddy.exceptions import (
    GraphqlError,
    NoFirmwareUpdateAvailableError,
    NoResponseError,
    UnexpectedResponseError,
)
from birdbuddy.feeder import (
    FeederControlError,
    FeederUpdateStatus,
    FirmwareUpdateBusyError,
    FirmwareUpdatePreconditionError,
    FirmwareUpdateUnknownStateError,
)
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LOGGER
from .control import _requires_reauth, _start_reauth
from .coordinator import BirdBuddyDataUpdateCoordinator
from .platform import async_register_feeder_entities
from .device import BirdBuddyDevice
from .entity import BirdBuddyMixin

MAX_ERRORS = 4
MONITOR_TIMEOUT = 30 * 60
POLL_INTERVAL = 15


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up firmware entities from a config entry."""
    coordinator: BirdBuddyDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_register_feeder_entities(
        entry,
        coordinator,
        async_add_entities,
        [
            lambda feeder: (
                BirdBuddyUpdate(feeder, coordinator) if feeder.is_owner else None
            ),
        ],
    )


class BirdBuddyUpdate(BirdBuddyMixin, UpdateEntity):
    """Firmware state separates a local monitor from the last backend result."""

    coordinator: BirdBuddyDataUpdateCoordinator
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )
    _attr_has_entity_name = True
    _attr_name = "Firmware Update"

    def __init__(
        self,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(feeder, coordinator)
        self._attr_unique_id = f"{self.feeder.id}-updater"
        self._attr_entity_registry_enabled_default = self.feeder.is_owner
        self._install_lock = asyncio.Lock()
        self._install_task: asyncio.Task[None] | None = None
        self._monitoring = False
        self._closing = False
        self._last_firmware_status: FeederUpdateStatus | None = None

    @property
    def available(self) -> bool:
        """Only a current owner can start firmware changes."""
        return super().available and self.feeder.is_owner and not self._closing

    @property
    def installed_version(self) -> str | None:
        """Return the latest observed installed version."""
        return self.feeder.version

    @property
    def latest_version(self) -> str | None:
        """Return the available version, or installed version when current."""
        return self.feeder.version_update_available or self.feeder.version

    @property
    def in_progress(self) -> bool | int | None:
        """Report local monitoring only, with server progress when available."""
        if not self._monitoring:
            return False
        status = self._last_firmware_status
        progress = status.progress if status and status.is_in_progress else None
        return int(progress) if progress else True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Keep the last server outcome after an interrupted local monitor."""
        status = self._last_firmware_status
        if status is None:
            return {"firmware_monitoring": self._monitoring}
        if status.is_complete:
            state = "succeeded"
        elif status.is_failed:
            state = "failed"
        elif status.is_in_progress:
            state = "in_progress"
        else:
            state = "unknown"
        return {
            "firmware_monitoring": self._monitoring,
            "firmware_server_status": state,
            "firmware_server_progress": status.progress,
            "firmware_failure_reason": status.failure_reason,
        }

    def _save_status(self, result: FeederUpdateStatus) -> None:
        self._last_firmware_status = result
        if isinstance(result.get("feeder"), dict):
            self.feeder.update(result["feeder"])
        if not self._closing:
            self.async_write_ha_state()

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Run one bounded immediate update, or attach to existing backend work."""
        if not self.available:
            raise HomeAssistantError(
                "Firmware updates require an available owner device"
            )
        if self._install_lock.locked():
            raise HomeAssistantError("Firmware update monitoring is already active")
        if backup:
            raise HomeAssistantError("Firmware backups are not supported")
        if version and version != self.latest_version:
            raise HomeAssistantError(
                "Only the currently available firmware can be installed"
            )
        async with self._install_lock:
            self._monitoring = True
            self._install_task = self.hass.async_create_task(
                self._run_install(version), "Bird Buddy firmware update"
            )
            try:
                await self._install_task
            finally:
                self._monitoring = False
                self._install_task = None
                if not self._closing:
                    self.async_write_ha_state()

    async def _run_install(self, version: str | None) -> None:
        try:
            await asyncio.wait_for(
                self._monitor_firmware(version), timeout=MONITOR_TIMEOUT
            )
        except asyncio.TimeoutError as err:
            raise HomeAssistantError(
                "Firmware monitoring timed out; the device may still be updating"
            ) from err
        except NoFirmwareUpdateAvailableError as err:
            raise HomeAssistantError("No firmware update is available") from err
        except FirmwareUpdateBusyError as err:
            raise HomeAssistantError(
                "Firmware start is already active; recheck its state"
            ) from err
        except FirmwareUpdatePreconditionError as err:
            raise HomeAssistantError(
                "Firmware update preconditions are not fulfilled"
            ) from err
        except HomeAssistantError:
            raise
        except FirmwareUpdateUnknownStateError as err:
            if err.status is not None:
                self._save_status(err.status)
            raise HomeAssistantError(
                "The device reported an unknown firmware update state"
            ) from err
        except Exception as err:
            if _requires_reauth(err):
                _start_reauth(self)
                raise HomeAssistantError(
                    "Bird Buddy authentication expired; sign in again. "
                    "The device may still be updating"
                ) from None
            # Exception text and full result dictionaries can contain account data.
            raise HomeAssistantError(
                "Firmware operation could not be confirmed; recheck device state"
            ) from None

    async def _monitor_firmware(self, version: str | None) -> None:
        result = await self.coordinator.client.update_firmware_start(
            self.feeder, immediate=True, expected_version=version
        )
        errors = 0
        while True:
            self._save_status(result)
            if result.is_complete:
                await self.coordinator.async_request_refresh()
                LOGGER.info("Bird Buddy firmware update completed")
                return
            if result.is_failed:
                raise HomeAssistantError(
                    "The device reported a firmware update failure"
                )
            if result.is_unknown:
                raise HomeAssistantError(
                    "The device reported an unknown firmware update state"
                )
            await asyncio.sleep(POLL_INTERVAL)
            try:
                result = await self.coordinator.client.update_firmware_check(
                    self.feeder
                )
                errors = 0
            except FirmwareUpdateUnknownStateError:
                raise
            except (
                GraphqlError,
                NoResponseError,
                UnexpectedResponseError,
                FeederControlError,
            ) as err:
                if _requires_reauth(err):
                    # Expired credentials are not a transient progress error.
                    # Let the outer handler start reauth without retrying here.
                    raise
                errors += 1
                if errors >= MAX_ERRORS:
                    raise HomeAssistantError(
                        "Firmware monitoring lost contact; the device may still be updating"
                    ) from None
                LOGGER.warning(
                    "Firmware progress check failed (%d/%d): %s",
                    errors,
                    MAX_ERRORS,
                    type(err).__name__,
                )

    async def async_will_remove_from_hass(self) -> None:
        """Cancel and await the owned monitor; never cancel backend installation."""
        self._closing = True
        task = self._install_task
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._monitoring = False
        await super().async_will_remove_from_hass()
