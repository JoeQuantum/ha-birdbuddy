"""Optional service routing and lifecycle; no live account requests or mutations."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from birdbuddy.exceptions import AuthenticationFailedError, GraphqlError
from birdbuddy.insights import AccountInsights
from birdbuddy.user import BirdBuddyUser
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CoreState, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.birdbuddy import _setup_services
from custom_components.birdbuddy.const import DOMAIN
from custom_components.birdbuddy.insights_service import (
    INSIGHT_KINDS,
    SERVICE_GET_ACCOUNT_INSIGHTS,
)
from tests.coordinator_helpers import (
    accounts_factory as accounts_factory,
    hass_config_dir as hass_config_dir,
    hass_storage as hass_storage,
    isolated_storage_directory as isolated_storage_directory,
)


@pytest.fixture(autouse=True)
def register_service(hass):
    hass.set_state(CoreState.running)
    _setup_services(hass)


@pytest.fixture
def sdk_factory():
    adapters = []

    def create(client):
        adapter = MagicMock(spec=AccountInsights)
        for kind in INSIGHT_KINDS:

            async def read(*args, _kind=kind, **kwargs):
                return {
                    "schema_version": 1,
                    "account_id": client.user["id"],
                    "resource": _kind,
                    "status": "available",
                    "data": {"fixture": True},
                }

            setattr(adapter, kind, AsyncMock(side_effect=read))
        adapter.async_close = AsyncMock()
        adapters.append(adapter)
        return adapter

    with patch(
        "custom_components.birdbuddy.insights_service.AccountInsights",
        side_effect=create,
    ) as factory:
        yield factory, adapters


async def call(hass, data, *, response=True):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_ACCOUNT_INSIGHTS,
        data,
        blocking=True,
        return_response=response,
    )


async def test_service_registration_and_polling_are_idle(
    hass, accounts_factory, sdk_factory
):
    factory, _ = sdk_factory
    account = accounts_factory()
    await account.poll()
    assert (
        hass.services.supports_response(DOMAIN, SERVICE_GET_ACCOUNT_INSIGHTS)
        is SupportsResponse.ONLY
    )
    assert account.coordinator.account_insights is None
    factory.assert_not_called()
    with pytest.raises(HomeAssistantError):
        await call(hass, {"kind": "hardware"}, response=False)
    factory.assert_not_called()


@pytest.mark.parametrize("kind", INSIGHT_KINDS)
async def test_each_kind_routes_only_compatible_parameters(
    hass, accounts_factory, sdk_factory, kind
):
    _, adapters = sdk_factory
    account = accounts_factory()
    await account.poll()
    data = {"kind": kind, "config_entry_id": account.entry.entry_id}
    if kind == "species":
        data["species_id"] = "species-a"
    else:
        data["limit"] = 12
    result = await call(hass, data)
    assert result["resource"] == kind
    assert result["account_id"] == "account-a"
    assert result["config_entry_id"] == account.entry.entry_id
    method = getattr(adapters[0], kind)
    if kind == "species":
        method.assert_awaited_once_with("species-a")
    elif kind == "feeder_report":
        method.assert_awaited_once_with(feeder_id=None, start=None, end=None, limit=12)
    else:
        method.assert_awaited_once_with(limit=12)
    for other in set(INSIGHT_KINDS) - {kind}:
        getattr(adapters[0], other).assert_not_called()
    account.assert_no_mutations()


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"kind": "unknown"},
        {"kind": []},
        {"kind": "species"},
        {"kind": "hardware", "species_id": "species-a"},
        {"kind": "hardware", "feeder_id": "feeder-a"},
        {"kind": "hardware", "start": None},
        {"kind": "species", "species_id": "species-a", "limit": 50},
        {"kind": "named_birds", "userId": "foreign"},
        {"kind": "named_birds", "device_id": "device-a"},
        {"kind": "hardware", "limit": True},
        {"kind": "hardware", "limit": "50"},
        {"kind": "hardware", "limit": 0},
        {"kind": "hardware", "limit": 101},
        {"kind": "hardware", "limit": 1.5},
        {"kind": "hardware", "account_id": " "},
        {"kind": "species", "species_id": None},
        {"kind": "species", "species_id": ""},
        {"kind": "feeder_report", "start": "2026-01-01T00:00:00Z"},
        {"kind": "feeder_report", "start": None, "end": None},
        {"kind": "feeder_report", "start": "invalid", "end": "2026-01-02T00:00:00Z"},
        {"kind": "feeder_report", "start": "2026-01-01", "end": "2026-01-02"},
        {
            "kind": "feeder_report",
            "start": "2026-01-02T00:00:00Z",
            "end": "2026-01-01T00:00:00Z",
        },
        {
            "kind": "feeder_report",
            "start": "2026-01-01T00:00:00Z",
            "end": "2027-01-03T00:00:00Z",
        },
    ],
)
async def test_invalid_or_unused_inputs_are_rejected_before_adapter_creation(
    hass, accounts_factory, sdk_factory, data
):
    factory, _ = sdk_factory
    account = accounts_factory()
    await account.poll()
    with pytest.raises(ServiceValidationError):
        await call(hass, data)
    factory.assert_not_called()
    account.assert_no_mutations()


async def test_report_dates_and_known_feeder_are_checked_before_read(
    hass, accounts_factory, sdk_factory
):
    factory, adapters = sdk_factory
    account = accounts_factory()
    await account.poll()
    with pytest.raises(ServiceValidationError, match="authenticated account"):
        await call(
            hass,
            {
                "kind": "feeder_report",
                "feeder_id": "foreign",
                "account_id": "account-a",
            },
        )
    factory.assert_not_called()
    await call(
        hass,
        {
            "kind": "feeder_report",
            "feeder_id": "feeder-a",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-02-01T00:00:00Z",
        },
    )
    adapters[0].feeder_report.assert_awaited_once_with(
        feeder_id="feeder-a",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 2, 1, tzinfo=timezone.utc),
        limit=50,
    )


async def test_ambiguous_accounts_require_selection_and_conflicting_selectors_fail(
    hass, accounts_factory, sdk_factory
):
    factory, _ = sdk_factory
    first = accounts_factory()
    second = accounts_factory(account="account-b")
    await first.poll()
    await second.poll()
    with pytest.raises(ServiceValidationError, match="ambiguous"):
        await call(hass, {"kind": "hardware"})
    with pytest.raises(ServiceValidationError, match="conflict"):
        await call(
            hass,
            {
                "kind": "hardware",
                "config_entry_id": first.entry.entry_id,
                "account_id": "account-b",
            },
        )
    factory.assert_not_called()
    result = await call(hass, {"kind": "hardware", "account_id": "account-b"})
    assert result["config_entry_id"] == second.entry.entry_id
    factory.assert_called_once_with(second.client)
    first.assert_no_mutations()
    second.assert_no_mutations()


async def test_zero_feeder_account_uses_one_lazy_adapter_for_account_reads(
    hass, accounts_factory, sdk_factory
):
    factory, _ = sdk_factory
    account = accounts_factory(feeders=())
    await account.poll()
    await call(hass, {"kind": "remote_connections"})
    await call(hass, {"kind": "preferences"})
    factory.assert_called_once_with(account.client)
    assert not account.coordinator.feeders
    account.assert_no_mutations()


async def test_cached_and_concurrent_calls_use_one_real_sdk_read(
    hass, accounts_factory
):
    account = accounts_factory()
    await account.poll()
    entered, release = asyncio.Event(), asyncio.Event()

    async def read(**kwargs):
        entered.set()
        await release.wait()
        return {
            "me": {
                "user": {"id": "account-a"},
                "postcardSettings": {
                    "ignoreMysteryVisitors": False,
                    "ignoredSpecies": [],
                },
            }
        }

    account.client._make_request.side_effect = read
    first = asyncio.create_task(call(hass, {"kind": "preferences"}))
    second = asyncio.create_task(call(hass, {"kind": "preferences"}))
    await entered.wait()
    release.set()
    results = await asyncio.gather(first, second)
    cached = await call(hass, {"kind": "preferences"})
    assert results[0] == results[1] == cached
    account.client._make_request.assert_awaited_once()
    assert cached["complete"] is True
    account.assert_no_mutations()


async def test_identity_change_before_read_starts_reauth_without_constructing_sdk(
    hass, accounts_factory, sdk_factory
):
    factory, _ = sdk_factory
    account = accounts_factory()
    await account.poll()
    account.client._me = BirdBuddyUser(
        {"id": "foreign", "email": "foreign@example.invalid"}
    )
    with patch.object(type(account.entry), "async_start_reauth") as reauth:
        with pytest.raises(HomeAssistantError, match="authentication expired"):
            await call(hass, {"kind": "hardware"})
        reauth.assert_called_once_with(hass)
    factory.assert_not_called()


async def test_identity_change_during_read_never_returns_foreign_data(
    hass, accounts_factory, sdk_factory
):
    _, adapters = sdk_factory
    account = accounts_factory()
    await account.poll()
    await call(hass, {"kind": "hardware"})

    async def switched(**kwargs):
        account.client._me = BirdBuddyUser({"id": "foreign"})
        return {
            "schema_version": 1,
            "resource": "hardware",
            "account_id": "foreign",
            "data": {"secret": "foreign-data"},
        }

    adapters[0].hardware.side_effect = switched
    with patch.object(type(account.entry), "async_start_reauth") as reauth:
        with pytest.raises(HomeAssistantError, match="authentication expired") as error:
            await call(hass, {"kind": "hardware"})
        reauth.assert_called_once_with(hass)
    assert "foreign-data" not in str(error.value)


async def test_replaced_coordinator_cannot_return_a_late_result(
    hass, accounts_factory, sdk_factory
):
    _, adapters = sdk_factory
    original = accounts_factory()
    await original.poll()
    await call(hass, {"kind": "hardware"})

    async def replaced(**kwargs):
        replacement = accounts_factory(entry=original.entry)
        await replacement.poll()
        return {
            "schema_version": 1,
            "resource": "hardware",
            "account_id": "account-a",
            "data": {},
        }

    adapters[0].hardware.side_effect = replaced
    with pytest.raises(ServiceValidationError, match="reloaded"):
        await call(hass, {"kind": "hardware"})


async def test_unloaded_entry_cannot_return_late_response(
    hass, accounts_factory, sdk_factory
):
    _, adapters = sdk_factory
    account = accounts_factory()
    await account.poll()
    await call(hass, {"kind": "hardware"})

    async def unloaded(**kwargs):
        account.entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
        return {
            "schema_version": 1,
            "resource": "hardware",
            "account_id": "account-a",
            "data": {},
        }

    adapters[0].hardware.side_effect = unloaded
    with pytest.raises(ServiceValidationError, match="not loaded"):
        await call(hass, {"kind": "hardware"})


@pytest.mark.parametrize(
    "error",
    [
        AuthenticationFailedError(),
        GraphqlError({"message": "secret vendor payload"}),
        RuntimeError("secret vendor payload"),
    ],
)
async def test_sdk_errors_are_safe_and_authentication_starts_reauth(
    hass, accounts_factory, sdk_factory, error
):
    _, adapters = sdk_factory
    account = accounts_factory()
    await account.poll()
    await call(hass, {"kind": "hardware"})
    adapters[0].hardware.side_effect = error
    with patch.object(type(account.entry), "async_start_reauth") as reauth:
        with pytest.raises(HomeAssistantError) as raised:
            await call(hass, {"kind": "hardware"})
        assert "secret vendor payload" not in str(raised.value)
        assert raised.value.__suppress_context__
        assert reauth.call_count == int(isinstance(error, AuthenticationFailedError))


async def test_report_range_error_preserves_other_reads_and_account_health(
    hass, accounts_factory
):
    """A report error is safe, uncached and independent of polling and other ranges."""
    account = accounts_factory()
    await account.poll()
    good = {
        "kind": "feeder_report",
        "feeder_id": "feeder-a",
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-02-01T00:00:00+00:00",
    }
    bad = good | {"start": "2025-12-01T00:00:00+00:00", "end": good["start"]}

    async def read(*, query, variables):
        if variables["start"] == bad["start"]:
            raise GraphqlError(
                {
                    "message": "synthetic-private-server-detail",
                    "extensions": {"code": "INTERNAL_SERVER_ERROR"},
                }
            )
        return {
            "me": {"user": {"id": "account-a"}},
            "userFeederReport": {
                "userId": "account-a",
                "feederIds": ["feeder-a"],
                "reportLabel": "Synthetic period",
                "reportPeriod": {
                    "startDate": variables["start"],
                    "endDate": variables["end"],
                },
                "visitAggregateMetrics": {
                    "totalNumberOfVisits": 7,
                    "numberOfUniqueSpecies": 2,
                    "busiestHourOfDay": "2026-01-02T12:00:00Z",
                    "speciesVisitSummaries": [],
                },
            },
        }

    account.client._make_request.side_effect = read
    with patch.object(type(account.entry), "async_start_reauth") as reauth:
        cached = await call(hass, good)
        for attempt in range(2):
            with pytest.raises(HomeAssistantError, match="GraphqlError") as error:
                await call(hass, bad)
            assert "synthetic-private-server-detail" not in str(error.value)
            assert error.value.__suppress_context__
            assert account.client._make_request.await_count == attempt + 2
            assert await call(hass, good) == cached
            assert account.client._make_request.await_count == attempt + 2
        reauth.assert_not_called()
    await account.poll()
    assert account.coordinator.last_update_success
    assert account.entry.state is ConfigEntryState.LOADED
    assert not account.coordinator._active_work
    account.assert_no_mutations()


async def test_shutdown_closes_the_adapter_and_cancels_its_shielded_read(
    hass, accounts_factory
):
    account = accounts_factory()
    await account.poll()
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def blocked(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    account.client._make_request.side_effect = blocked
    task = asyncio.create_task(call(hass, {"kind": "preferences"}))
    await entered.wait()
    adapter = account.coordinator.account_insights
    await account.coordinator.async_shutdown()
    result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert cancelled.is_set()
    assert adapter._closed
    assert not adapter._inflight
    assert not adapter._cache
    assert not account.coordinator._active_work
    with pytest.raises(ServiceValidationError, match="unloading"):
        await call(hass, {"kind": "preferences"})
    account.client._make_request.assert_awaited_once()


async def test_cached_result_is_not_returned_after_account_identity_changes(
    hass, accounts_factory
):
    account = accounts_factory()
    await account.poll()
    account.client._make_request.side_effect = None
    account.client._make_request.return_value = {
        "me": {
            "user": {"id": "account-a"},
            "postcardSettings": {"ignoreMysteryVisitors": False, "ignoredSpecies": []},
        }
    }
    await call(hass, {"kind": "preferences"})
    account.client._me = BirdBuddyUser({"id": "foreign"})
    with patch.object(type(account.entry), "async_start_reauth") as reauth:
        with pytest.raises(HomeAssistantError, match="authentication expired"):
            await call(hass, {"kind": "preferences"})
        reauth.assert_called_once_with(hass)
    account.client._make_request.assert_awaited_once()


@pytest.mark.parametrize(
    "field,value",
    [("account_id", "foreign"), ("resource", "named_birds"), ("schema_version", 2)],
)
async def test_inconsistent_sdk_response_is_not_returned(
    hass, accounts_factory, sdk_factory, field, value
):
    _, adapters = sdk_factory
    account = accounts_factory()
    await account.poll()
    await call(hass, {"kind": "hardware"})
    adapters[0].hardware.side_effect = None
    adapters[0].hardware.return_value = {
        "schema_version": 1,
        "account_id": "account-a",
        "resource": "hardware",
        "data": {"secret": "do-not-return"},
        field: value,
    }
    with pytest.raises(HomeAssistantError) as error:
        await call(hass, {"kind": "hardware"})
    assert "do-not-return" not in str(error.value)
