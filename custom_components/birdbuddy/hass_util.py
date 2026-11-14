"""Resolve account identity and action targets without arbitrary fallbacks."""

from collections.abc import Mapping

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_ACCOUNT_ID, DOMAIN
from .coordinator import BirdBuddyDataUpdateCoordinator


def _account_id_for_entry(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Read a persisted UUID, or the authenticated user of a legacy entry."""
    stored = entry.data.get(CONF_ACCOUNT_ID)
    if isinstance(stored, str) and stored:
        return stored
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    user = getattr(getattr(coordinator, "client", None), "user", None)
    account_id = user.get("id") if isinstance(user, Mapping) else None
    return account_id if isinstance(account_id, str) and account_id else None


def _feeder_id_for_device(hass: HomeAssistant, device_id: str) -> str:
    """Return the vendor UUID associated with one HA device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ValueError("Bird Buddy device was not found")
    ids = [identifier for domain, identifier in device.identifiers if domain == DOMAIN]
    if len(ids) != 1:
        raise ValueError("Device does not identify one Bird Buddy feeder")
    return ids[0]


def _device_config_entry_ids(device: object) -> set[str]:
    """Support both HA's single-account devices and older shared devices."""
    # HA 2026.11 scopes device identifiers to one config entry. Older releases
    # keep the shared device's account associations in config_entries instead.
    if getattr(device, "is_composite_device", False):
        # Current HA preserves old shared-device IDs as composite views. Their
        # primary config_entry_id is not an explicit account selection.
        return set(getattr(device, "config_entries", ()))
    if isinstance(entry_id := getattr(device, "config_entry_id", None), str):
        return {entry_id}
    return set(getattr(device, "config_entries", ()))


def _resolve_coordinator(
    hass: HomeAssistant,
    *,
    config_entry_id: str | None = None,
    account_id: str | None = None,
    feeder_id: str | None = None,
    device_id: str | None = None,
) -> BirdBuddyDataUpdateCoordinator:
    """Resolve intersecting selectors and reject conflicts before an action.

    A historical/removed feeder can use an explicitly selected account, or a
    sole configured account. Multiple accounts and shared feeders require an
    unambiguous selection even if only one account is currently loaded.
    """
    entries = {
        entry.entry_id: entry for entry in hass.config_entries.async_entries(DOMAIN)
    }
    coordinators = hass.data.get(DOMAIN, {})
    candidates = set(entries)
    if not candidates:
        raise ValueError("No Bird Buddy account is configured")

    if config_entry_id is not None:
        if config_entry_id not in entries:
            raise ValueError("The selected Bird Buddy config entry was not found")
        candidates.intersection_update({config_entry_id})

    if account_id is not None:
        matches = {
            entry_id
            for entry_id, entry in entries.items()
            if _account_id_for_entry(hass, entry) == account_id
        }
        if not matches:
            raise ValueError("The selected Bird Buddy account ID was not found")
        candidates.intersection_update(matches)

    registry = dr.async_get(hass)
    if device_id is not None:
        device_feeder_id = _feeder_id_for_device(hass, device_id)
        if feeder_id is not None and feeder_id != device_feeder_id:
            raise ValueError("The selected device and postcard feeder do not match")
        feeder_id = device_feeder_id
        device = registry.async_get(device_id)
        assert device is not None  # Validated above in this synchronous callback.
        candidates.intersection_update(_device_config_entry_ids(device))

    if feeder_id is not None:
        matches = {
            entry_id
            for entry_id, coordinator in coordinators.items()
            if entry_id in entries
            and coordinator is not None
            and feeder_id in getattr(coordinator, "feeders", {})
        }
        # Registry associations retain ambiguity for shared but unloaded accounts.
        # Newer HA versions can have several devices with this same vendor ID,
        # so looking up only one device would silently drop another account.
        for entry_id in entries:
            if any(
                (DOMAIN, feeder_id) in device.identifiers
                for device in dr.async_entries_for_config_entry(registry, entry_id)
            ):
                matches.add(entry_id)
        if matches:
            candidates.intersection_update(matches)
        elif config_entry_id is None and account_id is None and len(entries) != 1:
            raise ValueError(
                "Feeder account is unknown; select config_entry_id or account_id"
            )

    if not candidates:
        raise ValueError("Bird Buddy account, feeder and device targets conflict")
    if len(candidates) != 1:
        raise ValueError(
            "Bird Buddy account is ambiguous; select config_entry_id or account_id"
        )
    entry_id = next(iter(candidates))
    entry = entries[entry_id]
    coordinator = coordinators.get(entry_id)
    if entry.state is not ConfigEntryState.LOADED or coordinator is None:
        raise ValueError(
            "The selected Bird Buddy account is not loaded; load or reauthenticate it"
        )
    return coordinator


def _find_coordinator_by_feeder(
    hass: HomeAssistant, feeder_id: str
) -> BirdBuddyDataUpdateCoordinator | None:
    """Find a unique matching runtime feeder, retaining the legacy helper API."""
    matches = [
        coordinator
        for coordinator in hass.data.get(DOMAIN, {}).values()
        if coordinator is not None and feeder_id in getattr(coordinator, "feeders", {})
    ]
    if not matches:
        return None
    return _resolve_coordinator(hass, feeder_id=feeder_id)


def _find_coordinator_by_device(
    hass: HomeAssistant, device_id: str
) -> BirdBuddyDataUpdateCoordinator:
    """Resolve a device to one loaded account, including shared-device checks."""
    return _resolve_coordinator(hass, device_id=device_id)
