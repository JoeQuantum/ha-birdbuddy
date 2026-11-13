"""Bird Buddy sensors"""

from __future__ import annotations
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from birdbuddy.media import Media
from birdbuddy.feeder import MetricState

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfTemperature,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LOGGER
from .coordinator import BirdBuddyDataUpdateCoordinator
from .entity import BirdBuddyMixin
from .platform import async_register_feeder_entities
from .device import BirdBuddyDevice
from .visitors import RecentVisitor, RecentVisitors
from .util import media_preview_url


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
            lambda feeder: BirdBuddyBatteryEntity(feeder, coordinator),
            lambda feeder: BirdBuddySignalEntity(feeder, coordinator),
            lambda feeder: BirdBuddyStateEntity(feeder, coordinator),
            lambda feeder: BirdBuddyLastSyncEntity(feeder, coordinator),
            lambda feeder: BirdBuddyRecentVisitorEntity(feeder, coordinator),
            lambda feeder: BirdBuddyFoodStateEntity(feeder, coordinator),
            lambda feeder: BirdBuddyTemperatureEntity(feeder, coordinator),
        ],
    )


class BirdBuddyBatteryEntity(BirdBuddyMixin, SensorEntity):
    """Representation of a Bird Buddy battery."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Battery"

    def __init__(
        self,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(feeder, coordinator)
        self._attr_unique_id = f"{self.feeder.id}-battery"

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        return self.feeder.battery.percentage

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        return {"level": self.feeder.battery.state.value}


class BirdBuddySignalEntity(BirdBuddyMixin, SensorEntity):
    """Bird Buddy wifi signal strength."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_has_entity_name = True
    _attr_name = "Signal Strength"

    def __init__(
        self,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(feeder, coordinator)
        self._attr_unique_id = f"{self.feeder.id}-signal"

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        return self.feeder.signal.rssi

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        return {"level": self.feeder.signal.state.value}


class BirdBuddyRecentVisitorEntity(BirdBuddyMixin, RestoreSensor):
    """Bird Buddy recent visitors"""

    _attr_has_entity_name = True
    _attr_icon = "mdi:bird"
    _attr_name = "Recent Visitor"

    _latest_media: Media | None = None

    def __init__(
        self,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(feeder, coordinator)
        self._attr_unique_id = f"{self.feeder.id}-recent-visitor"
        self._recent_visitors: list[RecentVisitor] = []

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Expose the recent-visitor carousel as a list attribute.

        Kept minimal (species, media_url, created_at) so the recorded
        attribute payload stays well under HA's recorder cap.
        """
        return {
            "visitors": [
                {
                    "species": entry.species.name if entry.species else None,
                    "media_url": media_preview_url(entry.media),
                    "created_at": (
                        entry.created_at.isoformat()
                        if isinstance(entry.created_at, datetime)
                        else (str(entry.created_at) if entry.created_at else None)
                    ),
                }
                for entry in self._recent_visitors
            ],
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.add_visitor_listener(
                self.feeder,
                self._on_recent_visitor,
            )
        )

    @property
    def entity_picture(self) -> str | None:
        return media_preview_url(self._latest_media)

    @property
    def native_value(self) -> str | None:
        if attr := super().native_value:
            # Postcard listener set the attribute directly, use it
            return str(attr)
        return None

    @callback
    def _on_recent_visitor(self, visitors: RecentVisitors) -> None:
        self._recent_visitors = list(visitors.recent)
        media = visitors.latest_media
        species = visitors.latest_species
        self._latest_media = media
        self._attr_entity_picture = media_preview_url(media)
        self._attr_native_value = species.name if species else None
        self.async_write_ha_state()


class BirdBuddyStateEntity(BirdBuddyMixin, SensorEntity):
    """Bird Buddy Feeder state."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_has_entity_name = True
    _attr_icon = "mdi:bird"
    _attr_name = "Feeder State"
    _attr_options = [
        # See birdbuddy/feeder.py, FeederState enum values
        "deep_sleep",
        "factory_reset",
        "firmware_update",
        "offline",
        "off_grid",
        "online",
        "out_of_feeder",
        "pending_factory_reset",
        "pending_removal",
        "ready_to_stream",
        "streaming",
        "taking_postcards",
        # anything unexpected
        "unknown",
    ]
    _attr_translation_key = "feeder_state"

    def __init__(
        self,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(feeder, coordinator)
        self._attr_unique_id = f"{self.feeder.id}-state"

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        return self.feeder.state.value.lower()


class BirdBuddyLastSyncEntity(BirdBuddyMixin, SensorEntity):
    """Timestamp of the last successful Bird Buddy poll.

    The value is account-wide (one coordinator serves all feeders), but the
    entity is attached per feeder device to match the existing per-feeder model
    and avoid introducing a synthetic account-level device. See README note.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "last_sync"

    def __init__(
        self,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(feeder, coordinator)
        self._attr_unique_id = f"{self.feeder.id}-last-sync"

    @property
    def native_value(self) -> datetime | None:
        """The UTC time of the last successful update."""
        return self.coordinator.last_update_timestamp


class BirdBuddyTemperatureEntity(BirdBuddyMixin, SensorEntity):
    """Bird Buddy feeder temperature"""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_entity_registry_enabled_default = False  # Incubating
    _attr_entity_category = EntityCategory.DIAGNOSTIC  # Incubating
    _attr_has_entity_name = True
    _attr_name = "Temperature"
    # TODO: remove once it is verified working
    _attr_attribution = "(This entity is incubating)"
    # FIXME: value is always 0, cannot tell unit
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(feeder, coordinator)
        self._attr_unique_id = f"{self.feeder.id}-temperature"

    @property
    def native_value(self) -> float | None:
        """Temperature reported by the feeder"""
        return self.feeder.temperature

    async def add_to_platform_finish(self) -> None:
        await super().add_to_platform_finish()
        if self.enabled:
            LOGGER.warning("Bird Buddy Temperature entity is incubating")


class BirdBuddyFoodStateEntity(BirdBuddyMixin, SensorEntity):
    """Bird Buddy Food/Seed level."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False  # Incubating
    _attr_has_entity_name = True
    _attr_icon = "mdi:food-turkey"
    _attr_name = "Food Level"
    _attr_translation_key = "metric_state"
    _attr_options = [
        "low",
        "medium",
        "high",
    ]
    # TODO: remove once it is verified working
    _attr_attribution = "(This entity is incubating)"

    def __init__(
        self,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(feeder, coordinator)
        self._attr_unique_id = f"{self.feeder.id}-food-state"

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        state = self.feeder.food
        return state.value.lower() if state is not MetricState.UNKNOWN else None

    async def add_to_platform_finish(self) -> None:
        await super().add_to_platform_finish()
        if self.enabled:
            LOGGER.warning("Bird Buddy Food Level entity is incubating")
