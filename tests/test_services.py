"""Actual HA service routing, explicit intent and durable action boundaries."""

import asyncio
import json
import traceback
from pathlib import Path
from unittest.mock import AsyncMock, patch

from birdbuddy.exceptions import (
    AuthenticationFailedError,
    GraphqlError,
    NoResponseError,
    PostcardOutcomeUnknownError,
)
from birdbuddy.postcards import CollectedPostcard, CollectionReceipt, PostcardAnalysis
from birdbuddy.transport import HttpResponseError
from homeassistant.core import CoreState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
import pytest
import voluptuous as vol

from custom_components.birdbuddy import _setup_services
from custom_components.birdbuddy.const import (
    DOMAIN,
    EVENT_NEW_POSTCARD,
    EVENT_POSTCARD_BACKFILL,
)
from tests.coordinator_helpers import (
    accounts_factory as accounts_factory,
    card,
    hass_config_dir as hass_config_dir,
    hass_storage as hass_storage,
    isolated_storage_directory as isolated_storage_directory,
)
from tests.fixtures import load_fixture


@pytest.fixture(autouse=True)
def registered_services(hass):
    hass.set_state(CoreState.running)
    _setup_services(hass)


def stored(account):
    return json.loads(Path(account.coordinator.intake._store.path).read_text())["data"]


def request(account, **extra):
    return {
        "config_entry_id": account.entry.entry_id,
        "postcard_id": "card-a",
        "selection_policy": "server",
        **extra,
    }


async def call(hass, name, data):
    return await hass.services.async_call(
        DOMAIN, name, data, blocking=True, return_response=True
    )


def successful_collection(account, *, collected_id="collected-a"):
    async def collect(item_id, *, share=False, identify=False):
        journal = stored(account)["actions"][item_id]
        assert journal["status"] == "in_progress"
        assert journal["intent"] == {
            "operation": "collect",
            "share": share,
            "identify": identify,
        }
        return CollectedPostcard(
            card(collected_id, kind="FeedItemCollectedPostcard"),
            receipt=CollectionReceipt(item_id, share_requested=share),
        )

    account.client.collect_postcard.side_effect = collect


async def test_collection_persists_before_mutation_and_returns_confirmed_receipt(
    hass, accounts_factory
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    successful_collection(account)
    result = await call(hass, "collect_postcard", request(account, share=True))
    assert result == {
        "requested_postcard_id": "card-a",
        "collected_postcard_id": "collected-a",
        "is_first_collected_postcard": None,
        "reconciled_by_read": False,
        "share_requested": True,
        "sharing_confirmed": None,
    }
    assert stored(account)["actions"]["card-a"]["status"] == "succeeded"
    account.client.collect_postcard.assert_awaited_once_with(
        "card-a", share=True, identify=False
    )
    account.client.identify_postcard.assert_not_called()
    account.client.share_medias.assert_not_called()
    account.client.refresh_collections.assert_awaited_once_with(force=True)


@pytest.mark.parametrize(
    "extra",
    [
        {"strategy": "best_guess"},
        {"best_guess_confidence": 0.8},
        {"sighting": {}},
        {"mediaSpeciesItems": []},
        {"selection_policy": "recognized_only"},
        {"selection_policy": None},
    ],
)
async def test_unsupported_policy_is_rejected_before_even_a_detail_read(
    hass, accounts_factory, extra
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    with pytest.raises(ServiceValidationError):
        await call(hass, "collect_postcard", request(account, **extra))
    account.client.get_feed_item.assert_not_called()
    account.assert_no_mutations()
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, "legacy_postcard_contract")
        is not None
    )


@pytest.mark.parametrize("field", ["share", "share_media", "identify"])
async def test_collection_booleans_are_not_coerced_from_strings(
    hass, accounts_factory, field
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    with pytest.raises(ServiceValidationError):
        await call(hass, "collect_postcard", request(account, **{field: "false"}))
    account.client.get_feed_item.assert_not_called()
    account.assert_no_mutations()


async def test_equivalent_legacy_aliases_work_and_report_their_removal(
    hass, accounts_factory
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    successful_collection(account)
    result = await call(
        hass,
        "collect_postcard",
        {
            "postcard": {"id": "card-a"},
            "selection_policy": "server",
            "share_media": True,
        },
    )
    assert result["share_requested"] is True
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, "deprecated_postcard_aliases")
        is not None
    )


@pytest.mark.parametrize(
    "extra", [{"postcard": {"id": "other"}}, {"share": False, "share_media": True}]
)
async def test_conflicting_aliases_are_rejected_before_reads(
    hass, accounts_factory, extra
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    with pytest.raises(ServiceValidationError, match="conflict"):
        await call(hass, "collect_postcard", request(account, **extra))
    account.client.get_feed_item.assert_not_called()
    account.assert_no_mutations()


async def test_shared_feeder_requires_explicit_account_before_collection(
    hass, accounts_factory
):
    first = accounts_factory(snapshot=[card()])
    second = accounts_factory(account="account-b", snapshot=[card()])
    await first.poll()
    await second.poll()
    data = {
        "postcard_id": "card-a",
        "selection_policy": "server",
        "feeder_id": "feeder-a",
    }
    with pytest.raises(ServiceValidationError, match="ambiguous"):
        await call(hass, "collect_postcard", data)
    first.client.get_feed_item.assert_not_called()
    second.client.get_feed_item.assert_not_called()
    successful_collection(second)
    result = await call(hass, "collect_postcard", data | {"account_id": "account-b"})
    assert result["requested_postcard_id"] == "card-a"
    second.client.collect_postcard.assert_awaited_once()
    first.assert_no_mutations()


async def test_conflicting_account_selectors_never_choose_a_fallback(
    hass, accounts_factory
):
    first = accounts_factory(snapshot=[card()])
    second = accounts_factory(account="account-b", snapshot=[card()])
    await first.poll()
    await second.poll()
    with pytest.raises(ServiceValidationError, match="conflict"):
        await call(hass, "collect_postcard", request(first, account_id="account-b"))
    first.client.get_feed_item.assert_not_called()
    second.client.get_feed_item.assert_not_called()
    first.assert_no_mutations()
    second.assert_no_mutations()


async def test_actual_postcard_feeder_must_match_selected_feeder(
    hass, accounts_factory
):
    account = accounts_factory(
        feeders=("feeder-a", "feeder-b"), snapshot=[card(feeder="feeder-b")]
    )
    await account.poll()
    with pytest.raises(ServiceValidationError, match="selected feeder"):
        await call(hass, "collect_postcard", request(account, feeder_id="feeder-a"))
    account.client.get_feed_item.assert_awaited_once_with("card-a")
    account.assert_no_mutations()
    assert stored(account)["actions"] == {}


async def test_detail_service_is_complete_read_only_and_explicitly_routed(
    hass, accounts_factory
):
    raw = load_fixture("postcard_long_urls.json")
    first = accounts_factory()
    second = accounts_factory(account="account-b", snapshot=[raw])
    await first.poll()
    await second.poll()
    result = await call(
        hass,
        "get_postcard_details",
        {"postcard_id": raw["id"], "account_id": "account-b"},
    )
    assert len(result["medias"]) == 25
    assert result["medias_complete"] is True
    assert result["config_entry_id"] == second.entry.entry_id
    assert result["account_id"] == "account-b"
    second.client.get_feed_item.assert_awaited_once_with(raw["id"])
    first.client.get_feed_item.assert_not_called()
    first.assert_no_mutations()
    second.assert_no_mutations()


async def test_successful_collection_cannot_be_repeated(hass, accounts_factory):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    successful_collection(account)
    await call(hass, "collect_postcard", request(account))
    with pytest.raises(ServiceValidationError, match="succeeded"):
        await call(hass, "collect_postcard", request(account))
    account.client.collect_postcard.assert_awaited_once()


@pytest.mark.parametrize(
    "error,identify,status",
    [
        (NoResponseError(), False, "unknown"),
        (
            PostcardOutcomeUnknownError("card-a", share_requested=False),
            False,
            "unknown",
        ),
        (GraphqlError({"message": "Synthetic resolver failure"}), False, "unknown"),
        (HttpResponseError(408), False, "unknown"),
        (HttpResponseError(403), False, "failed"),
        (HttpResponseError(403), True, "unknown"),
    ],
)
async def test_action_failure_records_uncertainty_without_replay(
    hass, accounts_factory, error, identify, status
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    account.client.collect_postcard.side_effect = error
    with pytest.raises(HomeAssistantError):
        await call(hass, "collect_postcard", request(account, identify=identify))
    record = stored(account)["actions"]["card-a"]
    assert record["status"] == status
    assert record["error_type"] == type(error).__name__
    if status == "unknown":
        with pytest.raises(ServiceValidationError, match="unknown"):
            await call(hass, "collect_postcard", request(account))
        account.client.collect_postcard.assert_awaited_once()


async def test_cancelled_collection_is_durably_unknown(hass, accounts_factory):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    entered = asyncio.Event()

    async def collect(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    account.client.collect_postcard.side_effect = collect
    task = asyncio.create_task(call(hass, "collect_postcard", request(account)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stored(account)["actions"]["card-a"]["status"] == "unknown"


async def test_acknowledging_unknown_outcome_never_performs_a_mutation(
    hass, accounts_factory
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    account.client.collect_postcard.side_effect = NoResponseError()
    with pytest.raises(HomeAssistantError):
        await call(hass, "collect_postcard", request(account))
    target = {"postcard_id": "card-a", "config_entry_id": account.entry.entry_id}
    for invalid in (False, "true", 1):
        with pytest.raises(vol.Invalid):
            await call(
                hass, "acknowledge_postcard_retry", target | {"confirm": invalid}
            )
    assert stored(account)["actions"]["card-a"]["status"] == "unknown"
    result = await call(hass, "acknowledge_postcard_retry", target | {"confirm": True})
    assert result == {
        "postcard_id": "card-a",
        "retry_allowed": True,
        "action_performed": False,
    }
    account.client.collect_postcard.assert_awaited_once()
    successful_collection(account)
    await call(hass, "collect_postcard", request(account))
    assert account.client.collect_postcard.await_count == 2


async def test_failed_durable_write_blocks_collection(hass, accounts_factory):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    with patch.object(account.coordinator.intake._store, "async_save", new=AsyncMock()):
        with pytest.raises(ServiceValidationError, match="not verified"):
            await call(hass, "collect_postcard", request(account))
    account.assert_no_mutations()
    assert stored(account)["actions"] == {}


async def test_explicit_identification_can_precede_collection(hass, accounts_factory):
    account = accounts_factory(snapshot=[card()])
    await account.poll()

    async def identify(item_id):
        assert stored(account)["actions"][item_id]["status"] == "in_progress"
        return PostcardAnalysis(card(item_id))

    account.client.identify_postcard.side_effect = identify
    result = await call(hass, "identify_postcard", {"postcard_id": "card-a"})
    assert result["postcard_id"] == "card-a"
    assert stored(account)["actions"]["card-a"]["intent"] == {"operation": "identify"}
    account.client.collect_postcard.assert_not_called()
    successful_collection(account)
    await call(hass, "collect_postcard", request(account))
    account.client.identify_postcard.assert_awaited_once()
    account.client.collect_postcard.assert_awaited_once_with(
        "card-a", share=False, identify=False
    )


async def test_collection_refresh_failure_does_not_erase_confirmed_success(
    hass, accounts_factory
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    successful_collection(account)
    account.client.refresh_collections.side_effect = NoResponseError()
    result = await call(hass, "collect_postcard", request(account))
    assert result["collected_postcard_id"] == "collected-a"
    assert stored(account)["actions"]["card-a"]["status"] == "succeeded"
    with pytest.raises(ServiceValidationError):
        await call(hass, "collect_postcard", request(account))
    account.client.collect_postcard.assert_awaited_once()


async def test_backfill_service_is_bounded_and_never_uses_live_event(
    hass, accounts_factory
):
    account = accounts_factory(snapshot=[card(f"old-{i}") for i in range(25)])
    await account.poll()
    live, historical = [], []
    hass.bus.async_listen(EVENT_NEW_POSTCARD, live.append)
    hass.bus.async_listen(EVENT_POSTCARD_BACKFILL, historical.append)
    for invalid in (0, 101):
        with pytest.raises(vol.Invalid):
            await call(hass, "backfill", {"max_pages": invalid})
    result = await call(hass, "backfill", {"account_id": "account-a", "max_pages": 2})
    await hass.async_block_till_done()
    assert result == {"queued": 5, "delivered": 5, "pending": 0}
    assert len(historical) == 5 and not live
    assert account.scan_arguments == [2]
    account.assert_no_mutations()


async def test_retry_failed_events_does_not_retry_unknown_actions(
    hass, accounts_factory
):
    account = accounts_factory()
    await account.poll()
    account.snapshot = [card()]
    with patch.object(
        account.coordinator,
        "_publish_intake_record",
        new=AsyncMock(side_effect=ValueError("synthetic error")),
    ):
        await account.poll()
    assert account.coordinator.intake.pending_count == 1
    intake = account.coordinator.intake
    await intake.async_begin_action("separate-card", {"operation": "collect"})
    await intake.async_finish_action("separate-card", "unknown")
    result = await call(hass, "retry_feed_items", {"postcard_id": "card-a"})
    assert result == {"retried": 1}
    assert intake.pending_count == 0
    assert intake.unknown_action_count == 1
    account.assert_no_mutations()


async def test_service_auth_failure_starts_reauthentication(hass, accounts_factory):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    account.client.get_feed_item.side_effect = AuthenticationFailedError()
    with patch.object(type(account.entry), "async_start_reauth") as reauth:
        with pytest.raises(HomeAssistantError, match="authentication expired"):
            await call(hass, "get_postcard_details", {"postcard_id": "card-a"})
        reauth.assert_called_once_with(hass)
    account.assert_no_mutations()


@pytest.mark.parametrize(
    "service",
    ["get_postcard_details", "backfill", "identify_postcard", "collect_postcard"],
)
@pytest.mark.parametrize("error_type", [GraphqlError, RuntimeError])
async def test_read_service_errors_do_not_expose_backend_payloads(
    hass, accounts_factory, service, error_type
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    private = "synthetic-private-vendor-detail?Signature=secret"
    error = (
        error_type({"message": private})
        if error_type is GraphqlError
        else error_type(private)
    )
    data = {"config_entry_id": account.entry.entry_id}
    if service != "backfill":
        account.client.get_feed_item.side_effect = error
        data["postcard_id"] = "card-a"
        if service == "collect_postcard":
            data["selection_policy"] = "server"
    else:
        account.intake_error = error
    with pytest.raises(HomeAssistantError) as caught:
        await call(hass, service, data)
    assert type(error).__name__ in str(caught.value)
    assert private not in "".join(traceback.format_exception(caught.value))
    account.assert_no_mutations()


async def test_selected_device_is_checked_against_the_actual_postcard(
    hass, accounts_factory, device_reg
):
    account = accounts_factory(
        feeders=("feeder-a", "feeder-b"), snapshot=[card(feeder="feeder-b")]
    )
    await account.poll()
    device = device_reg.async_get_or_create(
        config_entry_id=account.entry.entry_id, identifiers={(DOMAIN, "feeder-a")}
    )
    with pytest.raises(ServiceValidationError, match="selected feeder"):
        await call(hass, "collect_postcard", request(account, device_id=device.id))
    account.assert_no_mutations()
    assert stored(account)["actions"] == {}


async def test_concurrent_collection_calls_share_one_mutation_lock(
    hass, accounts_factory
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    entered, release = asyncio.Event(), asyncio.Event()

    async def collect(item_id, **kwargs):
        entered.set()
        await release.wait()
        return CollectedPostcard(
            card("collected-a", kind="FeedItemCollectedPostcard"),
            receipt=CollectionReceipt(item_id),
        )

    account.client.collect_postcard.side_effect = collect
    first = asyncio.create_task(call(hass, "collect_postcard", request(account)))
    await entered.wait()
    second = asyncio.create_task(call(hass, "collect_postcard", request(account)))
    await asyncio.sleep(0)
    account.client.collect_postcard.assert_awaited_once()
    release.set()
    result = await first
    assert result["collected_postcard_id"] == "collected-a"
    with pytest.raises(ServiceValidationError, match="succeeded"):
        await second
    account.client.collect_postcard.assert_awaited_once()


async def test_restart_keeps_unknown_action_blocked_without_another_mutation(
    hass, accounts_factory
):
    original = accounts_factory(snapshot=[card()])
    await original.poll()
    original.client.collect_postcard.side_effect = NoResponseError()
    with pytest.raises(HomeAssistantError):
        await call(hass, "collect_postcard", request(original))
    restarted = accounts_factory(entry=original.entry, snapshot=[card()])
    await restarted.poll()
    with pytest.raises(ServiceValidationError, match="unknown"):
        await call(hass, "collect_postcard", request(restarted))
    restarted.assert_no_mutations()
    assert restarted.coordinator.intake.unknown_action_count == 1


async def test_uninitialized_intake_refuses_actions_before_cloud_access(
    hass, accounts_factory
):
    account = accounts_factory(snapshot=[card()])
    with pytest.raises(ServiceValidationError, match="storage is not ready"):
        await call(hass, "collect_postcard", request(account))
    account.client.get_feed_item.assert_not_called()
    account.assert_no_mutations()
