"""The Bird Buddy integration."""

from __future__ import annotations

from birdbuddy.client import BirdBuddy

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    LOGGER,
    SERVICE_COLLECT_POSTCARD,
    SERVICE_SCHEMA_COLLECT_POSTCARD,
)
from .coordinator import BirdBuddyDataUpdateCoordinator
from .hass_util import _find_coordinator_by_feeder

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.IMAGE,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Unique-id suffixes for entities that were previously disabled-by-default but
# are now enabled-by-default. HA's entity registry persists `disabled_by` per
# entity, so flipping `_attr_entity_registry_enabled_default = True` in code
# does NOT retroactively enable already-registered entities; users would have
# to enable each one by hand. `_async_migrate_enabled_defaults` does it for
# them at integration load. Add to this tuple when promoting any other
# previously-disabled entity in future releases.
_PROMOTED_DISABLED_DEFAULT_SUFFIXES: tuple[str, ...] = ("-recent-visitor",)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Setup the integration"""
    # This will register the services even if there's no ConfigEntry yet...
    _setup_services(hass)
    return True


def _async_migrate_enabled_defaults(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Re-enable entities whose default was promoted from disabled to enabled."""
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if ent.disabled_by is not er.RegistryEntryDisabler.INTEGRATION:
            continue
        if not any(
            ent.unique_id.endswith(suffix)
            for suffix in _PROMOTED_DISABLED_DEFAULT_SUFFIXES
        ):
            continue
        LOGGER.info(
            "Re-enabling %s (default flipped from disabled to enabled in a "
            "newer release; HA's registry remembered the old default)",
            ent.entity_id,
        )
        registry.async_update_entity(ent.entity_id, disabled_by=None)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Set up Bird Buddy from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Migrate registry state BEFORE platforms set up, so the platforms create
    # their entities against the corrected disabled_by value.
    _async_migrate_enabled_defaults(hass, entry)

    client = BirdBuddy(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    client.language_code = hass.config.language
    coordinator = BirdBuddyDataUpdateCoordinator(hass, client, entry)

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await coordinator.async_config_entry_first_refresh()

    # Apply a changed polling interval without requiring a restart: reload the
    # entry when its options change, which rebuilds the coordinator with the new
    # update_interval.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(
        entry,
        PLATFORMS,
    )

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS,
    ):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """Remove a config entry from a device."""
    return True


def _setup_services(hass: HomeAssistant) -> bool:
    """Register the BirdBuddy service(s)"""

    async def handle_collect_postcard(service: ServiceCall) -> None:
        feeder_id = service.data["sighting"]["feeder"]["id"]
        coordinator: BirdBuddyDataUpdateCoordinator
        coordinator = _find_coordinator_by_feeder(hass, feeder_id)
        if not coordinator:
            # We could not find this specific feeder. This could mean that the Feeder has been
            # factory reset and re-paired, but the Feed belongs to the same user. If we assume
            # that, we can move on to find the next available Coordinator, even if it might not
            # have the same feeder id anymore.
            coordinator = next(iter(hass.data[DOMAIN].values()))
            if coordinator:
                LOGGER.warning(
                    "Feeder with id '%s' not found: trying %s",
                    feeder_id,
                    list(coordinator.feeders.keys()),
                )
            else:
                raise ValueError("Feeder with id '{feeder_id}' not found.")

        await coordinator.handle_collect_postcard(service.data)

    hass.services.async_register(
        DOMAIN,
        SERVICE_COLLECT_POSTCARD,
        handle_collect_postcard,
        schema=SERVICE_SCHEMA_COLLECT_POSTCARD,
    )
