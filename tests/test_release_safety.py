"""Adversarial release checks across the SDK and durable HA action boundary."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from birdbuddy.client import BirdBuddy
from birdbuddy.exceptions import AuthenticationFailedError, MutationOutcomeUnknownError
from birdbuddy.user import BirdBuddyUser
from homeassistant.components.media_source.error import MediaSourceError, Unresolvable
from homeassistant.components.media_source.models import MediaSourceItem
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.core import CoreState
import pytest

from custom_components.birdbuddy.postcard_policy import validate_collection_request
from custom_components.birdbuddy.intakestore import IntakePersistenceError
from custom_components.birdbuddy import _setup_services
from custom_components.birdbuddy.const import DOMAIN
from custom_components.birdbuddy.media_source import BirdBuddyMediaSource
from custom_components.birdbuddy.switch import BirdBuddyOffGridSwitch
from tests.coordinator_helpers import (
    accounts_factory as accounts_factory,
    card,
    hass_config_dir as hass_config_dir,
    hass_storage as hass_storage,
    isolated_storage_directory as isolated_storage_directory,
)


async def test_auth_failure_during_uncertain_collection_reconciliation_blocks_replay(
    hass, accounts_factory
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    client = account.client
    # Exercise the actual SDK collect/reconcile implementation with offline I/O.
    client.collect_postcard = BirdBuddy.collect_postcard.__get__(client, BirdBuddy)
    visit = await client.get_feed_item("card-a")
    client.get_feed_item.side_effect = [
        visit,
        AuthenticationFailedError("fixture-auth"),
    ]
    client._make_request.side_effect = MutationOutcomeUnknownError("response lost")
    request = validate_collection_request(
        {"postcard_id": "card-a", "selection_policy": "server"}
    )
    with patch.object(type(account.entry), "async_start_reauth") as reauth:
        with pytest.raises(HomeAssistantError, match="authentication expired"):
            await account.coordinator.handle_collect_postcard(request)
        reauth.assert_called_once_with(hass)
    intake = account.coordinator.intake
    assert intake._current()["actions"]["card-a"]["status"] == "unknown"
    client.get_feed_item.side_effect = None
    client.get_feed_item.return_value = visit
    with pytest.raises(ServiceValidationError, match="unknown"):
        await account.coordinator.handle_collect_postcard(request)
    client._make_request.assert_awaited_once()
    restarted = accounts_factory(entry=account.entry, snapshot=[card()])
    await restarted.poll()
    with pytest.raises(ServiceValidationError, match="unknown"):
        await restarted.coordinator.handle_collect_postcard(request)
    restarted.assert_no_mutations()


async def test_preflight_auth_failure_never_journals_or_attempts_a_mutation(
    hass, accounts_factory
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    account.client.get_feed_item.side_effect = AuthenticationFailedError()
    request = validate_collection_request(
        {"postcard_id": "card-a", "selection_policy": "server"}
    )
    with patch.object(type(account.entry), "async_start_reauth") as reauth:
        with pytest.raises(HomeAssistantError, match="authentication expired"):
            await account.coordinator.handle_collect_postcard(request)
        reauth.assert_called_once_with(hass)
    assert account.coordinator.intake._current()["actions"] == {}
    account.assert_no_mutations()


async def test_shutdown_cancels_old_action_before_new_store_owner_starts(
    hass, accounts_factory
):
    hass.set_state(CoreState.running)
    old = accounts_factory(snapshot=[card()])
    await old.poll()
    entered = asyncio.Event()

    async def collect(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    old.client.collect_postcard.side_effect = collect
    request = validate_collection_request(
        {"postcard_id": "card-a", "selection_policy": "server"}
    )
    action = asyncio.create_task(old.coordinator.handle_collect_postcard(request))
    await entered.wait()
    await old.coordinator.async_shutdown()
    with pytest.raises(asyncio.CancelledError):
        await action
    assert not old.coordinator._active_work
    path = Path(old.coordinator.intake._store.path)
    assert (
        json.loads(await asyncio.to_thread(path.read_text))["data"]["actions"][
            "card-a"
        ]["status"]
        == "unknown"
    )
    restarted = accounts_factory(entry=old.entry, snapshot=[card(), card("new-card")])
    await restarted.poll()
    before = await asyncio.to_thread(path.read_bytes)
    assert len(json.loads(before)["data"]["processed"]) == 2
    with pytest.raises(ServiceValidationError, match="unloading"):
        await old.coordinator.handle_collect_postcard(request)
    with pytest.raises(IntakePersistenceError, match="closed"):
        await old.coordinator.intake.async_acknowledge_action_retry("card-a")
    assert await asyncio.to_thread(path.read_bytes) == before
    old.client.collect_postcard.assert_awaited_once()


async def test_shutdown_waits_for_persistence_after_cancelling_poll(
    hass, accounts_factory
):
    hass.set_state(CoreState.running)
    old = accounts_factory(snapshot=[card()])
    await old.poll()
    old.snapshot.append(card("new-card"))
    entered, release = asyncio.Event(), asyncio.Event()
    real_save = old.coordinator.intake._store.async_save

    async def blocked_save(state):
        entered.set()
        await release.wait()
        await real_save(state)

    with patch.object(old.coordinator.intake._store, "async_save", blocked_save):
        poll = asyncio.create_task(old.poll())
        await entered.wait()
        shutdown = asyncio.create_task(old.coordinator.async_shutdown())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert not poll.done()
        # Even cancellation of the unload caller cannot abandon the old write.
        shutdown.cancel()
        await asyncio.sleep(0)
        assert not shutdown.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await poll
        with pytest.raises(asyncio.CancelledError):
            await shutdown
    assert old.coordinator.intake.pending_count == 1
    assert not old.coordinator._active_work
    restarted = accounts_factory(entry=old.entry, snapshot=old.snapshot)
    await restarted.poll()
    state = json.loads(
        await asyncio.to_thread(
            Path(restarted.coordinator.intake._store.path).read_text
        )
    )["data"]
    assert len(state["processed"]) == 2
    assert state["pending"] == {}


async def test_closed_owner_rejects_recovery_and_late_startup_work(
    hass, accounts_factory
):
    account = accounts_factory()
    await account.poll()
    await account.coordinator.async_shutdown()
    coordinator = account.coordinator
    coordinator.async_request_refresh = AsyncMock()
    await coordinator._async_started(None)
    coordinator.async_request_refresh.assert_not_awaited()
    operations = [
        coordinator.async_retry_feed_items(None),
        coordinator.async_acknowledge_postcard_retry("card-a"),
        coordinator.async_backfill(),
        coordinator.handle_identify_postcard({"postcard_id": "card-a"}),
        account.poll(),
    ]
    for operation in operations:
        with pytest.raises(ServiceValidationError, match="unloading"):
            await operation
    account.assert_no_mutations()


async def test_nested_work_registration_survives_inner_exit_until_shutdown(
    accounts_factory,
):
    account = accounts_factory()
    coordinator = account.coordinator
    entered = asyncio.Event()

    async def nested_work():
        async with coordinator.async_work():
            async with coordinator.async_work():
                assert list(coordinator._active_work.values()) == [2]
            assert list(coordinator._active_work.values()) == [1]
            entered.set()
            await asyncio.Event().wait()

    work = asyncio.create_task(nested_work())
    await entered.wait()
    await coordinator.async_shutdown()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert not coordinator._active_work


@pytest.mark.parametrize("phase", ["before_action", "during_preflight"])
async def test_identity_mismatch_blocks_actions_before_journal_and_mutation(
    hass, accounts_factory, phase
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    visit = await account.client.get_feed_item("card-a")
    wrong_user = BirdBuddyUser({"id": "another-account"})
    if phase == "before_action":
        account.client._me = wrong_user
        with pytest.raises(ConfigEntryAuthFailed):
            await account.poll()
    else:

        async def switched_read(item_id):
            account.client._me = wrong_user
            return visit

        account.client.get_feed_item.side_effect = switched_read
    request = validate_collection_request(
        {"postcard_id": "card-a", "selection_policy": "server"}
    )
    with patch.object(type(account.entry), "async_start_reauth") as reauth:
        with pytest.raises(HomeAssistantError, match="authentication expired"):
            await account.coordinator.handle_collect_postcard(request)
        reauth.assert_called_once_with(hass)
    assert account.coordinator.intake._current()["actions"] == {}
    account.assert_no_mutations()


async def test_identity_mismatch_after_feed_read_does_not_accept_or_emit_batch(
    accounts_factory,
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    checkpoint = account.client.export_feed_checkpoint()
    original = account.client.retrieve_feed_batch
    account.snapshot.append(card("new-card"))

    async def switched_batch(**kwargs):
        batch = await original(**kwargs)
        account.client._me = BirdBuddyUser({"id": "another-account"})
        return batch

    account.client.retrieve_feed_batch = switched_batch
    with pytest.raises(ConfigEntryAuthFailed):
        await account.poll()
    assert account.client.export_feed_checkpoint() == checkpoint
    assert account.coordinator.intake.pending_count == 0


async def test_detail_response_is_withheld_if_identity_changes_during_read(
    hass, accounts_factory
):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    visit = await account.client.get_feed_item("card-a")

    async def switched_read(item_id):
        account.client._me = BirdBuddyUser({"id": "another-account"})
        return visit

    account.client.get_feed_item.side_effect = switched_read
    _setup_services(hass)
    with patch.object(type(account.entry), "async_start_reauth") as reauth:
        with pytest.raises(HomeAssistantError, match="authentication expired"):
            await hass.services.async_call(
                DOMAIN,
                "get_postcard_details",
                {"postcard_id": "card-a", "config_entry_id": account.entry.entry_id},
                blocking=True,
                return_response=True,
            )
        reauth.assert_called_once_with(hass)


async def test_browser_response_is_withheld_if_identity_changes_during_read(
    hass, accounts_factory
):
    account = accounts_factory()
    await account.poll()

    async def switched_collections():
        account.client._me = BirdBuddyUser({"id": "another-account"})
        return {}

    account.client.refresh_collections.side_effect = switched_collections
    item = MediaSourceItem(hass, DOMAIN, account.entry.entry_id, None)
    with patch.object(type(account.entry), "async_start_reauth") as reauth:
        with pytest.raises(MediaSourceError, match="reauthentication"):
            await BirdBuddyMediaSource(hass).async_browse_media(item)
        reauth.assert_called_once_with(hass)


@pytest.mark.parametrize("playback", [False, True])
async def test_browser_sdk_auth_failure_requests_reauthentication(
    hass, accounts_factory, playback
):
    account = accounts_factory()
    await account.poll()
    account.client.refresh_collections.side_effect = AuthenticationFailedError(
        "PRIVATE"
    )
    account.client.collection = AsyncMock(
        side_effect=AuthenticationFailedError("PRIVATE")
    )
    identifier = account.entry.entry_id + ("#collection#media" if playback else "")
    item = MediaSourceItem(hass, DOMAIN, identifier, None)
    source = BirdBuddyMediaSource(hass)
    with patch.object(type(account.entry), "async_start_reauth") as reauth:
        with pytest.raises(
            Unresolvable if playback else MediaSourceError, match="reauthentication"
        ):
            if playback:
                await source.async_resolve_media(item)
            else:
                await source.async_browse_media(item)
        reauth.assert_called_once_with(hass)


async def test_switch_work_is_cancelled_and_drained_on_account_shutdown(
    hass, accounts_factory
):
    account = accounts_factory()
    await account.poll()
    account.coordinator.last_update_success = True
    feeder = account.coordinator.feeders["feeder-a"]
    feeder["offGrid"] = False
    entity = BirdBuddyOffGridSwitch(feeder, account.coordinator)
    entity.hass = hass
    entered = asyncio.Event()

    async def toggle(*args):
        entered.set()
        await asyncio.Event().wait()

    account.client.toggle_off_grid = AsyncMock(side_effect=toggle)
    action = asyncio.create_task(entity.async_turn_on())
    await entered.wait()
    await account.coordinator.async_shutdown()
    with pytest.raises(asyncio.CancelledError):
        await action
    assert feeder.is_off_grid is False
    assert not account.coordinator._active_work
