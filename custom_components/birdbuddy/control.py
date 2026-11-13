"""Present bounded SDK controls through Home Assistant without optimistic state."""

from collections.abc import Awaitable, Callable

from birdbuddy.exceptions import AuthenticationFailedError
from birdbuddy.feeder import Feeder, FeederControlError, FeederControlUnavailableError
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)

from .entity import BirdBuddyMixin


def _requires_reauth(error: BaseException) -> bool:
    seen: set[int] = set()
    while id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, AuthenticationFailedError):
            return True
        cause = error.__cause__ or error.__context__
        if cause is None:
            break
        error = cause
    return False


def _start_reauth(entity: BirdBuddyMixin) -> None:
    entity.coordinator.async_set_update_error(
        ConfigEntryAuthFailed("Bird Buddy authentication expired")
    )
    entity.coordinator.config_entry.async_start_reauth(entity.hass)


async def async_run_control(
    entity: BirdBuddyMixin, operation: Callable[[], Awaitable[Feeder]]
) -> None:
    """Run once, keep cancellation, and report completion uncertainty honestly."""
    async with entity.coordinator.async_work():
        await _async_run_control(entity, operation)


async def _async_run_control(
    entity: BirdBuddyMixin, operation: Callable[[], Awaitable[Feeder]]
) -> None:
    if not entity.available:
        raise ServiceValidationError("This Bird Buddy control is unavailable")
    try:
        entity.coordinator.verify_account_identity()
        result = await operation()
        entity.coordinator.verify_account_identity()
    except AuthenticationFailedError:
        _start_reauth(entity)
        raise HomeAssistantError(
            "Bird Buddy authentication expired; sign in again"
        ) from None
    except FeederControlUnavailableError:
        raise ServiceValidationError(
            "The account role or device capability does not allow this control"
        ) from None
    except FeederControlError as err:
        reauth = _requires_reauth(err)
        if reauth:
            _start_reauth(entity)
        if err.action_completed is True:
            message = "The device action completed, but refreshed state is unavailable"
        elif err.action_completed is False:
            message = "The device action did not complete"
        else:
            message = (
                "Device action completion is unknown; refresh state before retrying"
            )
        if reauth:
            message += "; sign in again"
        raise HomeAssistantError(message) from None
    except ValueError:
        raise ServiceValidationError("Unsupported Bird Buddy control value") from None
    except Exception:
        # Raw SDK/transport messages can contain private URLs or account data.
        raise HomeAssistantError(
            "Device action could not be confirmed; refresh state before retrying"
        ) from None
    if not isinstance(result, Feeder) or result.get("id") != entity.feeder.id:
        raise HomeAssistantError("Device action returned an unrecognized result")
    current = entity.coordinator.feeders.get(entity.feeder.id)
    if current is None or not current.is_owner:
        raise HomeAssistantError(
            "Device action completed while feeder membership changed; refresh state"
        )
    # The SDK returns observed state after its bounded verification, not a request
    # echo. Take a copy in case a caller shares the same model object with us.
    data = dict(result)
    current.clear()
    current.update(data)
    entity.coordinator.async_update_listeners()
