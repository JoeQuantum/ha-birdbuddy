"""Optional response-only account insights, created lazily for explicit requests."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from birdbuddy.exceptions import AuthenticationFailedError
from birdbuddy.insights import AccountInsights, InsightClosedError
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import voluptuous as vol

from .const import CONF_ACCOUNT_ID, CONF_CONFIG_ENTRY_ID, DOMAIN
from .coordinator import BirdBuddyDataUpdateCoordinator
from .hass_util import _resolve_coordinator

SERVICE_GET_ACCOUNT_INSIGHTS = "get_account_insights"
INSIGHT_KINDS = (
    "hardware",
    "preferences",
    "species",
    "named_birds",
    "weekly_recap",
    "yearly_review",
    "feeder_report",
    "remote_connections",
)
# Preserve types until intent validation: true is not an integer page limit.
_SERVICE_SCHEMA = vol.Schema(dict)


@dataclass(frozen=True)
class InsightRequest:
    """An explicit selected resource and compatible account-scoped arguments."""

    kind: str
    config_entry_id: str | None
    account_id: str | None
    feeder_id: str | None
    species_id: str | None
    start: datetime | None
    end: datetime | None
    limit: int


def _identifier(data: Mapping[str, Any], name: str) -> str | None:
    if name not in data:
        return None
    value = data[name]
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 512
    ):
        raise ServiceValidationError(
            f"{name} must be a nonempty ID of at most 512 characters"
        )
    return value


def _datetime(value: object) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ServiceValidationError(
                "start and end must be ISO datetimes with a timezone"
            ) from None
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ServiceValidationError("start and end must include a timezone")
    return value


def validate_insight_request(data: Mapping[str, Any]) -> InsightRequest:
    """Reject unused or inconsistent inputs before account routing or any read."""
    allowed = {
        "kind",
        CONF_ACCOUNT_ID,
        CONF_CONFIG_ENTRY_ID,
        "feeder_id",
        "species_id",
        "start",
        "end",
        "limit",
    }
    if set(data).difference(allowed):
        raise ServiceValidationError("Unsupported account-insight parameter")
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in INSIGHT_KINDS:
        raise ServiceValidationError("kind must select one supported account insight")
    if kind != "feeder_report" and {"feeder_id", "start", "end"}.intersection(data):
        raise ServiceValidationError(
            "feeder_id, start and end are only valid for feeder_report"
        )
    if kind != "species" and "species_id" in data:
        raise ServiceValidationError("species_id is only valid for species")
    if kind == "species" and "limit" in data:
        raise ServiceValidationError("limit is unused for the single-species lookup")
    species_id = _identifier(data, "species_id")
    if kind == "species" and species_id is None:
        raise ServiceValidationError("species_id is required for species")
    limit = data.get("limit", 50)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ServiceValidationError("limit must be an integer from 1 to 100")
    start = end = None
    if "start" in data or "end" in data:
        if "start" not in data or "end" not in data:
            raise ServiceValidationError("start and end must be supplied together")
        start, end = _datetime(data["start"]), _datetime(data["end"])
        if not timedelta(0) < end - start <= timedelta(days=366):
            raise ServiceValidationError(
                "Report range must be positive and at most 366 days"
            )
    return InsightRequest(
        kind,
        _identifier(data, CONF_CONFIG_ENTRY_ID),
        _identifier(data, CONF_ACCOUNT_ID),
        _identifier(data, "feeder_id"),
        species_id,
        start,
        end,
        limit,
    )


def _resolve(
    hass: HomeAssistant, request: InsightRequest
) -> BirdBuddyDataUpdateCoordinator:
    try:
        return _resolve_coordinator(
            hass,
            config_entry_id=request.config_entry_id,
            account_id=request.account_id,
            feeder_id=request.feeder_id,
        )
    except ValueError as err:
        raise ServiceValidationError(str(err)) from None


async def _read(adapter: AccountInsights, request: InsightRequest) -> dict[str, Any]:
    if request.kind == "species":
        assert request.species_id is not None
        return await adapter.species(request.species_id)
    if request.kind == "feeder_report":
        return await adapter.feeder_report(
            feeder_id=request.feeder_id,
            start=request.start,
            end=request.end,
            limit=request.limit,
        )
    readers = {
        "hardware": adapter.hardware,
        "preferences": adapter.preferences,
        "named_birds": adapter.named_birds,
        "weekly_recap": adapter.weekly_recap,
        "yearly_review": adapter.yearly_review,
        "remote_connections": adapter.remote_connections,
    }
    return await readers[request.kind](limit=request.limit)


def setup_insights_service(hass: HomeAssistant) -> None:
    """Register the service without creating adapters or requesting account data."""

    async def handle(service: ServiceCall) -> ServiceResponse:
        request = validate_insight_request(service.data)
        coordinator = _resolve(hass, request)
        try:
            async with coordinator.async_work():
                coordinator.verify_account_identity()
                if (
                    request.feeder_id is not None
                    and request.feeder_id not in coordinator.client.feeders
                ):
                    raise ServiceValidationError(
                        "The selected feeder is not in this authenticated account"
                    )
                if coordinator.account_insights is None:
                    coordinator.account_insights = AccountInsights(coordinator.client)
                result = await _read(coordinator.account_insights, request)
                coordinator.verify_account_identity()
                if _resolve(hass, request) is not coordinator:
                    raise ServiceValidationError("This Bird Buddy account was reloaded")
                if (
                    result.get("account_id") != coordinator.account_id
                    or result.get("schema_version") != 1
                    or result.get("resource") != request.kind
                ):
                    raise HomeAssistantError(
                        "Bird Buddy returned inconsistent account insights"
                    )
                return result | {
                    CONF_CONFIG_ENTRY_ID: coordinator.config_entry.entry_id
                }
        except AuthenticationFailedError:
            if (
                hass.data.get(DOMAIN, {}).get(coordinator.config_entry.entry_id)
                is coordinator
            ):
                coordinator.config_entry.async_start_reauth(hass)
            raise HomeAssistantError("Bird Buddy authentication expired") from None
        except ServiceValidationError:
            raise
        except InsightClosedError:
            raise ServiceValidationError(
                "This Bird Buddy account is unloading"
            ) from None
        except ValueError:
            raise ServiceValidationError(
                "Invalid Bird Buddy insight parameters or result size"
            ) from None
        except Exception as err:
            raise HomeAssistantError(
                f"Bird Buddy insight read failed ({type(err).__name__})"
            ) from None

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_ACCOUNT_INSIGHTS,
        handle,
        schema=_SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
