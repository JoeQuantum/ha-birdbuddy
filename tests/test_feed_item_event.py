"""Coordinator event contracts use durable identity and preserve media detail."""

from homeassistant.components.recorder.db_schema import EventData, MAX_EVENT_DATA_BYTES
from homeassistant.core import CoreState
from homeassistant.helpers import issue_registry as ir
import pytest

from custom_components.birdbuddy.const import (
    DOMAIN,
    EVENT_NEW_FEED_ITEM,
    EVENT_NEW_POSTCARD,
    EVENT_NEW_POSTCARD_SIGHTING,
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


def listen(hass):
    events = {
        kind: []
        for kind in (
            EVENT_NEW_POSTCARD,
            EVENT_NEW_FEED_ITEM,
            EVENT_NEW_POSTCARD_SIGHTING,
            EVENT_POSTCARD_BACKFILL,
        )
    }
    for kind, captured in events.items():
        hass.bus.async_listen(kind, captured.append)
    return events


async def test_new_postcard_publishes_modern_and_legacy_notifications_once(
    hass, accounts_factory
):
    hass.set_state(CoreState.running)
    account = accounts_factory()
    events = listen(hass)
    await account.poll()
    account.snapshot = [card()]
    await account.poll()
    await account.poll()
    await hass.async_block_till_done()
    assert len(events[EVENT_NEW_POSTCARD]) == len(events[EVENT_NEW_FEED_ITEM]) == 1
    modern = events[EVENT_NEW_POSTCARD][0].data
    legacy = events[EVENT_NEW_FEED_ITEM][0].data
    assert modern["schema_version"] == legacy["schema_version"] == 1
    assert modern["event_id"] == legacy["event_id"]
    assert modern["postcard_id"] == legacy["item_id"] == "card-a"
    assert modern["config_entry_id"] == account.entry.entry_id
    assert modern["account_id"] == "account-a"
    assert modern["feeder_id"] == "feeder-a"
    assert len(modern["medias"]) == 3
    assert modern["media_count"] == 3
    assert modern["medias_complete"] is True
    assert not events[EVENT_NEW_POSTCARD_SIGHTING]
    assert not events[EVENT_POSTCARD_BACKFILL]
    account.assert_no_mutations()


@pytest.mark.parametrize(
    "kind",
    [
        "FeedItemCollectedPostcard",
        "FeedItemSpeciesSighting",
        "FeedItemSpeciesUnlocked",
        "FeedItemMysteryVisitorNotRecognized",
        "FeedItemMysteryVisitorResolved",
    ],
)
async def test_other_visit_kinds_only_emit_the_feed_notification(
    hass, accounts_factory, kind
):
    hass.set_state(CoreState.running)
    account = accounts_factory()
    events = listen(hass)
    await account.poll()
    account.snapshot = [card(kind=kind)]
    await account.poll()
    await hass.async_block_till_done()
    assert [event.data["kind"] for event in events[EVENT_NEW_FEED_ITEM]] == [kind]
    assert not events[EVENT_NEW_POSTCARD]
    assert not events[EVENT_NEW_POSTCARD_SIGHTING]
    account.assert_no_mutations()


async def test_video_only_event_keeps_video_and_uses_thumbnail_for_preview(
    hass, accounts_factory
):
    hass.set_state(CoreState.running)
    account = accounts_factory()
    events = listen(hass)
    await account.poll()
    raw = load_fixture("postcard_video_only.json")
    account.snapshot = [raw]
    await account.poll()
    await hass.async_block_till_done()
    modern = events[EVENT_NEW_POSTCARD][0].data
    legacy = events[EVENT_NEW_FEED_ITEM][0].data
    assert modern["medias"][0]["kind"] == "MediaVideo"
    assert modern["preview_url"] == raw["medias"][0]["thumbnailUrl"]
    assert legacy["media_url"] is None
    assert legacy["thumbnail_url"] == modern["preview_url"]
    account.assert_no_mutations()


async def test_actual_published_event_fits_recorder_with_explicit_detail_route(
    hass, accounts_factory
):
    hass.set_state(CoreState.running)
    account = accounts_factory()
    events = listen(hass)
    await account.poll()
    account.snapshot = [load_fixture("postcard_long_urls.json")]
    await account.poll()
    await hass.async_block_till_done()
    for kind in (EVENT_NEW_POSTCARD, EVENT_NEW_FEED_ITEM):
        event = events[kind][0]
        encoded = EventData.shared_data_bytes_from_event(event, None)
        assert encoded != b"{}"
        assert len(encoded) < MAX_EVENT_DATA_BYTES
        assert event.data["details_required"] is True
        assert event.data["media_count"] == 25
        assert event.data["medias_complete"] is False
        assert (
            event.data["details"]["data"]["config_entry_id"] == account.entry.entry_id
        )
        assert event.data["event_id"].startswith("birdbuddy:")


async def test_backfill_is_separate_from_live_automation_events(hass, accounts_factory):
    hass.set_state(CoreState.running)
    history = [card(f"old-{i}") for i in range(25)]
    account = accounts_factory(snapshot=history)
    events = listen(hass)
    await account.poll()
    result = await account.coordinator.async_backfill(max_pages=2)
    await hass.async_block_till_done()
    assert result == {"queued": 5, "delivered": 5, "pending": 0}
    assert len(events[EVENT_POSTCARD_BACKFILL]) == 5
    assert all(
        event.data["source"] == "backfill" for event in events[EVENT_POSTCARD_BACKFILL]
    )
    assert not events[EVENT_NEW_POSTCARD]
    assert not events[EVENT_NEW_FEED_ITEM]
    assert not events[EVENT_NEW_POSTCARD_SIGHTING]
    assert account.scan_arguments == [2]
    account.assert_no_mutations()


async def test_nonvisitor_is_acknowledged_without_fabricating_a_postcard(
    hass, accounts_factory
):
    hass.set_state(CoreState.running)
    account = accounts_factory()
    events = listen(hass)
    await account.poll()
    account.snapshot = [
        {
            "id": "notice",
            "__typename": "FeedItemUnknownFuture",
            "createdAt": "2026-01-01T12:00:00Z",
        }
    ]
    await account.poll()
    await hass.async_block_till_done()
    assert not any(events.values())
    assert account.coordinator.intake.pending_count == 0
    account.assert_no_mutations()


async def test_legacy_listener_gets_a_repair_instead_of_sighting_mutations(
    hass, accounts_factory
):
    hass.set_state(CoreState.running)
    account = accounts_factory()
    events = listen(hass)
    await account.poll()
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, "legacy_postcard_contract")
        is not None
    )
    assert not events[EVENT_NEW_POSTCARD_SIGHTING]
    account.assert_no_mutations()
