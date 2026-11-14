"""Bird Buddy Selectors"""

from __future__ import annotations

from birdbuddy.feeder import Feeder, FeederControlError, PowerProfile

from homeassistant.components.select import (
    SelectEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
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
                BirdBuddyPowerProfileSelector(feeder, coordinator)
                if feeder.is_owner
                else None
            ),
        ],
    )


class BirdBuddyPowerProfileSelector(BirdBuddyMixin, SelectEntity):
    """Select Power Profile"""

    _attr_has_entity_name = True
    _attr_name = "Power Profile"
    _attr_icon = "mdi:power-settings"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "power_profile"

    def __init__(
        self,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(feeder, coordinator)
        self._attr_unique_id = f"{self.feeder.id}-power-profile"

    @property
    def options(self) -> list[str]:
        """Dropdown options derived from the PowerProfile enum.

        Order follows the enum definition order in pybirdbuddy. A new power
        mode added to PowerProfile shows up here automatically — but it also
        needs a matching key under `power_profile.state` in `strings.json`
        (and ideally each `translations/*.json`), or HA will fall back to
        displaying the raw value string.
        """
        return [p.value.lower() for p in PowerProfile if p is not PowerProfile.UNKNOWN]

    @property
    def current_option(self) -> str | None:
        """The selected setting, separate from Smart Energy's running mode."""
        if not self.feeder.is_owner:
            return None
        if self.feeder.get("powerProfile") not in {
            profile.value
            for profile in PowerProfile
            if profile is not PowerProfile.UNKNOWN
        }:
            return None
        return self.feeder.selected_power_profile.value.lower()

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.feeder.is_owner
            and self.current_option is not None
        )

    async def async_select_option(self, option: str) -> None:
        options = {
            profile.value.lower(): profile
            for profile in PowerProfile
            if profile is not PowerProfile.UNKNOWN
        }
        if not isinstance(option, str) or option not in options:
            raise ServiceValidationError("Unsupported Bird Buddy power profile")

        async def set_profile() -> Feeder:
            # This compatibility API returns the selected field only. Its full
            # verification refresh is available in the account-scoped cache.
            await self.coordinator.client.set_power_profile(
                self.feeder, options[option]
            )
            confirmed = self.coordinator.client.feeders.get(self.feeder.id)
            if confirmed is None:
                raise FeederControlError(
                    "Completed control has no confirmed feeder", action_completed=True
                )
            return confirmed

        await async_run_control(self, set_profile)
