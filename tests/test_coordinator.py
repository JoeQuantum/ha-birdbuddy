"""Account polling preserves telemetry, startup arrivals and current visitor data."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from birdbuddy.exceptions import AuthenticationFailedError, NoResponseError
from homeassistant.core import CoreState
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.birdbuddy.const import CONF_POLLING_INTERVAL, EVENT_NEW_POSTCARD
from tests.coordinator_helpers import (
    accounts_factory as accounts_factory,
    card,
    hass_config_dir as hass_config_dir,
    hass_storage as hass_storage,
    isolated_storage_directory as isolated_storage_directory,
)


@pytest.mark.parametrize("minutes", [None, 1, 3, 20])
async def test_poll_interval_and_config_entry_are_preserved(accounts_factory, minutes):
    account = accounts_factory(
        options={} if minutes is None else {CONF_POLLING_INTERVAL: minutes}
    )
    assert account.coordinator.update_interval == timedelta(minutes=minutes or 10)
    assert account.coordinator.config_entry is account.entry


async def test_startup_baseline_does_not_lose_arrivals(hass, accounts_factory):
    hass.set_state(CoreState.starting)
    history = [card(f"old-{i}") for i in range(20)]
    account = accounts_factory(snapshot=history)
    events = []
    hass.bus.async_listen(EVENT_NEW_POSTCARD, events.append)
    await account.poll()
    assert account.coordinator.intake.is_bootstrapped
    account.snapshot.insert(0, card("during-startup"))
    await account.poll()
    await hass.async_block_till_done()
    assert not events
    assert account.coordinator.intake.pending_count == 1
    hass.set_state(CoreState.running)
    await account.poll()
    await hass.async_block_till_done()
    assert [event.data["postcard_id"] for event in events] == ["during-startup"]
    account.assert_no_mutations()


async def test_empty_bootstrap_keeps_more_than_twenty_arrivals_live(
    hass, accounts_factory
):
    hass.set_state(CoreState.running)
    account = accounts_factory()
    events = []
    hass.bus.async_listen(EVENT_NEW_POSTCARD, events.append)
    await account.poll()
    account.snapshot = [card(f"new-{i}") for i in range(25)]
    await account.poll()
    await hass.async_block_till_done()
    assert len(events) == 25
    assert all(event.data["source"] == "live" for event in events)
    assert account.scan_arguments == [100]
    account.assert_no_mutations()


async def test_same_id_species_and_url_changes_refresh_without_an_event(
    hass, accounts_factory
):
    hass.set_state(CoreState.running)
    account = accounts_factory(snapshot=[card()])
    events = []
    hass.bus.async_listen(EVENT_NEW_POSTCARD, events.append)
    await account.poll()
    account.snapshot[0]["medias"][0]["contentUrl"] = (
        "https://example.invalid/current.jpg"
    )
    account.snapshot[0]["sightingReportPreview"]["sightings"][0]["species"]["name"] = (
        "Updated bird"
    )
    await account.poll()
    await hass.async_block_till_done()
    visitors = account.coordinator.visitors["feeder-a"]
    assert visitors.latest_media.content_url == "https://example.invalid/current.jpg"
    assert visitors.latest_species.name == "Updated bird"
    assert not events
    assert (
        sum(
            call.kwargs.get("first") == 100
            for call in account.client.feed.await_args_list
        )
        == 2
    )
    account.assert_no_mutations()


async def test_recovered_pending_payload_cannot_overwrite_fresh_visitor_state(
    hass, accounts_factory
):
    hass.set_state(CoreState.starting)
    account = accounts_factory()
    await account.poll()
    account.snapshot = [card()]
    await account.poll()
    assert account.coordinator.intake.pending_count == 1
    account.snapshot[0]["medias"][0]["contentUrl"] = "https://example.invalid/new.jpg"
    hass.set_state(CoreState.running)
    await account.poll()
    assert (
        account.coordinator.visitors["feeder-a"].latest_media.content_url
        == "https://example.invalid/new.jpg"
    )


async def test_restart_restores_checkpoint_and_pending_arrivals(hass, accounts_factory):
    hass.set_state(CoreState.starting)
    original = accounts_factory(snapshot=[card("history")])
    await original.poll()
    original.snapshot.insert(0, card("pending"))
    await original.poll()
    checkpoint = original.client.export_feed_checkpoint()
    restarted = accounts_factory(entry=original.entry, snapshot=original.snapshot)
    events = []
    hass.bus.async_listen(EVENT_NEW_POSTCARD, events.append)
    hass.set_state(CoreState.running)
    await restarted.poll()
    await hass.async_block_till_done()
    assert restarted.client.export_feed_checkpoint() == checkpoint
    assert [event.data["postcard_id"] for event in events] == ["pending"]
    assert restarted.coordinator.intake.pending_count == 0


@pytest.mark.parametrize(
    "error,expected",
    [
        (AuthenticationFailedError(), ConfigEntryAuthFailed),
        (NoResponseError(), UpdateFailed),
    ],
)
async def test_telemetry_auth_and_transport_failures_are_distinct(
    accounts_factory, error, expected
):
    account = accounts_factory()
    account.client.refresh.side_effect = error
    with pytest.raises(expected):
        await account.poll()
    assert account.coordinator.last_update_timestamp is None


async def test_feed_failure_preserves_telemetry_and_checkpoint(accounts_factory):
    account = accounts_factory(snapshot=[card()])
    await account.poll()
    checkpoint = account.client.export_feed_checkpoint()
    account.intake_error = NoResponseError()
    assert await account.poll() is account.client
    assert account.coordinator.last_update_timestamp is not None
    assert account.coordinator.feed_error_type == "NoResponseError"
    assert account.client.export_feed_checkpoint() == checkpoint
    account.assert_no_mutations()


@pytest.mark.parametrize("stage", ["intake", "display", "dispatch"])
async def test_secondary_auth_failures_remain_reauthentication_errors(
    hass, accounts_factory, stage
):
    hass.set_state(CoreState.running)
    account = accounts_factory()
    await account.poll()
    if stage == "intake":
        account.intake_error = AuthenticationFailedError()
    elif stage == "display":
        account.display_error = AuthenticationFailedError()
    else:
        account.snapshot = [card()]
    with patch.object(
        account.coordinator,
        "_publish_intake_record",
        new=AsyncMock(side_effect=AuthenticationFailedError()),
    ):
        with pytest.raises(ConfigEntryAuthFailed):
            await account.poll()


async def test_one_unrepresentable_event_does_not_block_the_next(
    hass, accounts_factory
):
    hass.set_state(CoreState.running)
    account = accounts_factory()
    await account.poll()
    account.snapshot = [card("x" * 31_000), card("good")]
    events = []
    hass.bus.async_listen(EVENT_NEW_POSTCARD, events.append)
    await account.poll()
    await hass.async_block_till_done()
    assert [event.data["postcard_id"] for event in events] == ["good"]
    assert account.coordinator.intake.pending_count == 1
    account.assert_no_mutations()
