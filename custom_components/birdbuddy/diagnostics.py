"""Small allowlisted diagnostics without account identifiers or media payloads."""

from collections import Counter
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
import platform

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN


def _versions() -> dict[str, str]:
    result = {"python": platform.python_version()}
    for package in ("homeassistant", "pybirdbuddy", "python-graphql-client"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "missing"
    return result


def _stamp(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Export counts and safe categories; never serialize client/config entry data."""
    versions = await hass.async_add_executor_job(_versions)
    versions["integration"] = str((await async_get_integration(hass, DOMAIN)).version)
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    result = {"versions": versions, "loaded": coordinator is not None}
    if coordinator is None:
        return result
    roles = Counter()
    for feeder in coordinator.feeders.values():
        kind = feeder.get("__typename")
        roles[
            {
                "FeederForOwner": "owner",
                "FeederForMember": "member",
                "FeederForMemberPending": "pending",
                "FeederForPublic": "public",
            }.get(kind, "unknown")
        ] += 1
    checkpoint = coordinator.client.export_feed_checkpoint()
    intake = coordinator.intake
    # Restrict error categories rather than trusting an arbitrary exception string.
    error = coordinator.feed_error_type
    safe_error = (
        error
        if error
        in {
            "IntakeError",
            "IntakePersistenceError",
            "IntakeCapacityError",
            "PaginationError",
            "AuthenticationFailedError",
            "NoResponseError",
            "HttpResponseError",
            "GraphqlError",
            "UnexpectedResponseError",
            "TimeoutError",
            "ValueError",
        }
        else "other"
        if error
        else None
    )
    result.update(
        {
            "telemetry": {
                "last_update_success": coordinator.last_update_success,
                "last_success": _stamp(coordinator.last_update_timestamp),
                "feeder_count": len(coordinator.feeders),
                "roles": dict(roles),
            },
            "postcards": {
                "last_success": _stamp(coordinator.last_feed_success),
                "error_category": safe_error,
                "checkpoint_has_watermark": checkpoint.get("watermark") is not None,
                "checkpoint_id_count": len(checkpoint.get("seen", {})),
                "storage_initialized": bool(intake and intake.initialized),
                "bootstrapped": bool(intake and intake.is_bootstrapped),
                "pending_count": intake.pending_count if intake else 0,
                "dead_letter_count": intake.dead_letter_count if intake else 0,
                "unknown_action_count": intake.unknown_action_count if intake else 0,
            },
        }
    )
    return result
