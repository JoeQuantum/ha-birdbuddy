"""Bird Buddy switches"""

from typing import Any


from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .control import async_run_control
from .coordinator import BirdBuddyDataUpdateCoordinator
from .platform import async_register_feeder_entities
from .device import BirdBuddyDevice
from .entity import BirdBuddyMixin


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entities from a config entry."""
    coordinator: BirdBuddyDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_register_feeder_entities(
        entry,
        coordinator,
        async_add_entities,
        [
            lambda feeder: (
                BirdBuddyAudioSwitch(feeder, coordinator)
                if feeder.is_owner and feeder.supports_audio is not False
                else None
            ),
            lambda feeder: (
                BirdBuddyOffGridSwitch(feeder, coordinator) if feeder.is_owner else None
            ),
        ],
    )


class BirdBuddyOffGridSwitch(BirdBuddyMixin, SwitchEntity):
    """Off-grid switch"""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Off-Grid"
    _attr_has_entity_name = True
    coordinator: BirdBuddyDataUpdateCoordinator

    def __init__(
        self,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(feeder, coordinator)
        self._attr_unique_id = f"{self.feeder.id}-offgrid"

    @property
    def available(self) -> bool:
        return super().available and self.feeder.is_owner

    @property
    def is_on(self) -> bool | None:
        return self.feeder.is_off_grid if self.feeder.is_owner else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await async_run_control(
            self, lambda: self.coordinator.client.toggle_off_grid(self.feeder, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await async_run_control(
            self, lambda: self.coordinator.client.toggle_off_grid(self.feeder, False)
        )


class BirdBuddyAudioSwitch(BirdBuddyMixin, SwitchEntity):
    """Audio switch"""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Audio"
    _attr_icon = "mdi:microphone"
    _attr_has_entity_name = True
    _attr_translation_key = "audio_enabled"
    coordinator: BirdBuddyDataUpdateCoordinator

    def __init__(
        self,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(feeder, coordinator)
        self._attr_unique_id = f"{self.feeder.id}-audio"

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.feeder.is_owner
            and self.feeder.supports_audio is not False
        )

    @property
    def is_on(self) -> bool | None:
        return (
            self.feeder.is_audio_enabled
            if self.feeder.is_owner and self.feeder.supports_audio is not False
            else None
        )

    @property
    def icon(self) -> str | None:
        return "mdi:microphone" if self.is_on else "mdi:microphone-off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await async_run_control(
            self,
            lambda: self.coordinator.client.toggle_audio_enabled(self.feeder, True),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await async_run_control(
            self,
            lambda: self.coordinator.client.toggle_audio_enabled(self.feeder, False),
        )
