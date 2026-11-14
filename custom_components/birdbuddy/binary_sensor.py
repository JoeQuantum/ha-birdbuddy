"""Bird Buddy sensors"""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .platform import async_register_feeder_entities
from .device import BirdBuddyDevice
from .entity import BirdBuddyMixin
from .const import DOMAIN
from .coordinator import BirdBuddyDataUpdateCoordinator


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
        [lambda feeder: BirdBuddyChargingEntity(feeder, coordinator)],
    )


class BirdBuddyChargingEntity(BirdBuddyMixin, BinarySensorEntity):
    """Whether the Bird Buddy battery is charging."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Charging"
    _attr_has_entity_name = True

    def __init__(
        self,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(feeder, coordinator)
        self._attr_unique_id = f"{self.feeder.id}-charging"

    @property
    def is_on(self) -> bool | None:
        """Return the state of the sensor."""
        return self.feeder.battery.is_charging
