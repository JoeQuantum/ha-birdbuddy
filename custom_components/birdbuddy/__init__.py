"""The Bird Buddy integration."""

from __future__ import annotations

from birdbuddy.client import BirdBuddy
from birdbuddy.exceptions import AuthenticationFailedError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, CONF_DEVICE_ID, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ACCOUNT_ID,
    CONF_CONFIG_ENTRY_ID,
    DOMAIN,
    LOGGER,
    SERVICE_BACKFILL,
    SERVICE_ACKNOWLEDGE_POSTCARD_RETRY,
    SERVICE_RETRY_FEED_ITEMS,
    SERVICE_SCHEMA_ACKNOWLEDGE_RETRY,
    SERVICE_SCHEMA_RETRY_FEED_ITEMS,
    SERVICE_COLLECT_POSTCARD,
    SERVICE_GET_POSTCARD_DETAILS,
    SERVICE_IDENTIFY_POSTCARD,
    SERVICE_SCHEMA_BACKFILL,
    SERVICE_SCHEMA_POSTCARD_ID,
    SERVICE_SCHEMA_COLLECT_POSTCARD,
)
from .coordinator import BirdBuddyDataUpdateCoordinator
from .hass_util import _resolve_coordinator
from .events import async_get_postcard_details
from .intakestore import IntakeError
from .postcard_policy import UnsupportedPostcardPolicy, validate_collection_request
from .repairs import report_legacy_aliases, report_legacy_postcard_contract

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


def _async_migrate_enabled_defaults(hass: HomeAssistant, entry: ConfigEntry) -> None:
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
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # A failed setup is not an actionable account target.
        await coordinator.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise

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
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """Remove a config entry from a device."""
    return True


def _setup_services(hass: HomeAssistant) -> None:
    """Register explicit account actions and a complete read-only detail response."""

    def resolve(data) -> BirdBuddyDataUpdateCoordinator:
        try:
            return _resolve_coordinator(
                hass,
                feeder_id=data.get("feeder_id"),
                config_entry_id=data.get(CONF_CONFIG_ENTRY_ID),
                account_id=data.get(CONF_ACCOUNT_ID),
                device_id=data.get(CONF_DEVICE_ID),
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def handle_collect_postcard(service: ServiceCall) -> ServiceResponse:
        # Validate intent before account resolution, reads or any mutations.
        try:
            request = validate_collection_request(service.data)
        except UnsupportedPostcardPolicy as err:
            report_legacy_postcard_contract(hass)
            raise ServiceValidationError(str(err)) from err
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        if request.legacy_aliases:
            report_legacy_aliases(hass)
        coordinator = resolve(service.data)
        return await coordinator.handle_collect_postcard(request)

    async def handle_details(service: ServiceCall) -> ServiceResponse:
        coordinator = resolve(service.data)
        async with coordinator.async_work():
            coordinator.verify_account_identity()
            result = await async_get_postcard_details(
                coordinator.client,
                service.data["postcard_id"],
                config_entry_id=coordinator.config_entry.entry_id,
                account_id=coordinator.account_id,
            )
            coordinator.verify_account_identity()
            if resolve(service.data) is not coordinator:
                raise ServiceValidationError("This Bird Buddy account was reloaded")
            coordinator.validate_visit_target(result.get("feeder_id"), service.data)
            return result

    async def handle_identify(service: ServiceCall) -> ServiceResponse:
        coordinator = resolve(service.data)
        return await coordinator.handle_identify_postcard(service.data)

    async def handle_backfill(service: ServiceCall) -> ServiceResponse:
        coordinator = resolve(service.data)
        return await coordinator.async_backfill(max_pages=service.data["max_pages"])

    async def handle_retry_feed(service: ServiceCall) -> ServiceResponse:
        coordinator = resolve(service.data)
        retried = await coordinator.async_retry_feed_items(
            service.data.get("postcard_id")
        )
        return {"retried": retried}

    async def handle_acknowledge(service: ServiceCall) -> ServiceResponse:
        coordinator = resolve(service.data)
        item_id = service.data["postcard_id"]
        await coordinator.async_acknowledge_postcard_retry(item_id)
        return {
            "postcard_id": item_id,
            "retry_allowed": True,
            "action_performed": False,
        }

    def guarded(handler):
        async def call(service: ServiceCall) -> ServiceResponse:
            try:
                return await handler(service)
            except AuthenticationFailedError as err:
                resolve(service.data).config_entry.async_start_reauth(hass)
                raise HomeAssistantError("Bird Buddy authentication expired") from err
            except IntakeError as err:
                raise ServiceValidationError(str(err)) from err

        return call

    for name, handler, schema, response in (
        (
            SERVICE_RETRY_FEED_ITEMS,
            handle_retry_feed,
            SERVICE_SCHEMA_RETRY_FEED_ITEMS,
            SupportsResponse.OPTIONAL,
        ),
        (
            SERVICE_ACKNOWLEDGE_POSTCARD_RETRY,
            handle_acknowledge,
            SERVICE_SCHEMA_ACKNOWLEDGE_RETRY,
            SupportsResponse.OPTIONAL,
        ),
        (
            SERVICE_COLLECT_POSTCARD,
            handle_collect_postcard,
            SERVICE_SCHEMA_COLLECT_POSTCARD,
            SupportsResponse.OPTIONAL,
        ),
        (
            SERVICE_GET_POSTCARD_DETAILS,
            handle_details,
            SERVICE_SCHEMA_POSTCARD_ID,
            SupportsResponse.ONLY,
        ),
        (
            SERVICE_IDENTIFY_POSTCARD,
            handle_identify,
            SERVICE_SCHEMA_POSTCARD_ID,
            SupportsResponse.OPTIONAL,
        ),
        (
            SERVICE_BACKFILL,
            handle_backfill,
            SERVICE_SCHEMA_BACKFILL,
            SupportsResponse.OPTIONAL,
        ),
    ):
        hass.services.async_register(
            DOMAIN,
            name,
            guarded(handler),
            schema=schema,
            supports_response=response,
        )
