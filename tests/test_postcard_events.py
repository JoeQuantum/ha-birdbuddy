"""Complete mixed-media events fit recorder; oversize media remain discoverable."""

from unittest.mock import AsyncMock, MagicMock
from typing import TypedDict

import pytest
from birdbuddy.visits import normalize_visit
from homeassistant.components.recorder.db_schema import EventData, MAX_EVENT_DATA_BYTES
from homeassistant.core import Event

from custom_components.birdbuddy.events import (
    EVENT_DATA_BUDGET_BYTES,
    async_get_postcard_details,
    build_legacy_feed_item_event,
    build_postcard_event,
    event_data_size,
    postcard_details,
)
from tests.fixtures import load_fixture


class _AccountContext(TypedDict):
    config_entry_id: str
    account_id: str


ACCOUNT: _AccountContext = {"config_entry_id": "entry-a", "account_id": "account-a"}


@pytest.mark.parametrize(
    "name",
    [
        "postcard_complete.json",
        "postcard_video_only.json",
        "postcard_unrecognized.json",
        "public_collected_partial.json",
        "feed_postcard_minimal.json",
    ],
)
def test_ordinary_events_retain_every_image_video_and_optional_field(name):
    raw = load_fixture(name)
    if name == "feed_postcard_minimal.json":
        raw = raw["edges"][0]["node"]
    visit = normalize_visit(raw)
    payload = build_postcard_event(visit, **ACCOUNT)
    assert payload["schema_version"] == 1
    assert payload["postcard_id"] == visit.item_id
    assert payload["media_count"] == len(visit.medias)
    assert [media["id"] for media in payload["medias"]] == [
        media.id for media in visit.medias
    ]
    assert [media["kind"] for media in payload["medias"]] == [
        media.kind for media in visit.medias
    ]
    assert payload["medias_complete"] is True
    assert payload["species_complete"] is True
    assert payload["details_required"] is False
    assert payload["omitted_fields"] == []
    assert payload["details"]["data"] == {
        "postcard_id": visit.item_id,
        "config_entry_id": "entry-a",
    }


def test_video_only_preserves_video_and_exposes_only_thumbnail_as_preview():
    visit = normalize_visit(load_fixture("postcard_video_only.json"))
    payload = build_postcard_event(visit, **ACCOUNT)
    assert payload["media"]["kind"] == "MediaVideo"
    assert payload["preview_url"] == visit.medias[0].thumbnail_url
    assert payload["medias"][0]["content_url"] == visit.medias[0].content_url
    legacy = build_legacy_feed_item_event(visit, **ACCOUNT)
    assert legacy["media_url"] is None
    assert legacy["thumbnail_url"] == visit.medias[0].thumbnail_url


@pytest.mark.parametrize(
    "builder", [build_postcard_event, build_legacy_feed_item_event]
)
def test_long_urls_are_bounded_using_actual_recorder_encoding(builder):
    raw = load_fixture("postcard_long_urls.json")
    visit = normalize_visit(raw)
    original = visit.raw
    full = postcard_details(visit, **ACCOUNT)
    assert event_data_size(full) > MAX_EVENT_DATA_BYTES
    payload = builder(visit, **ACCOUNT)
    assert EVENT_DATA_BUDGET_BYTES < MAX_EVENT_DATA_BYTES
    assert event_data_size(payload) <= EVENT_DATA_BUDGET_BYTES
    recorded = EventData.shared_data_bytes_from_event(
        Event("birdbuddy_new_postcard", payload), None
    )
    assert recorded != b"{}"
    assert len(recorded) == event_data_size(payload)
    assert payload["details_required"] is True
    assert payload["medias_complete"] is False
    assert payload["media_count"] == len(visit.medias)
    assert "medias" in payload["omitted_fields"]
    assert payload["details"]["service"] == "birdbuddy.get_postcard_details"
    assert visit.raw == original
    assert len(full["medias"]) == len(visit.medias)
    assert all(media["content_url"] for media in full["medias"])


def test_one_huge_url_and_multibyte_species_are_declared_not_silently_lost():
    raw = load_fixture("postcard_complete.json")
    raw["medias"][0]["contentUrl"] = "https://example.invalid/" + "x" * 40_000
    raw["sightingReportPreview"]["sightings"][0]["species"]["name"] = "鳥" * 20_000
    visit = normalize_visit(raw)
    payload = build_postcard_event(visit, **ACCOUNT)
    assert event_data_size(payload) < EVENT_DATA_BUDGET_BYTES
    assert payload["species_count"] == 1
    assert payload["species_complete"] is False
    assert "species" in payload["omitted_fields"]
    assert payload["media_complete"] is False
    assert "media.content_url" in payload["omitted_fields"]
    assert postcard_details(visit, **ACCOUNT)["species"][0]["name"] == "鳥" * 20_000


def test_raw_tokens_and_reports_never_enter_event_or_detail_contract():
    raw = load_fixture("postcard_complete.json")
    raw["reportToken"] = "synthetic-private-report"
    raw["sightingReport"] = {
        "reportToken": "synthetic-report",
        "matchTokens": ["synthetic-match"],
    }
    visit = normalize_visit(raw)
    for builder in (
        build_postcard_event,
        build_legacy_feed_item_event,
        postcard_details,
    ):
        result = builder(visit, **ACCOUNT)
        assert "sighting" not in result
        assert "reportToken" not in str(result)
        assert "synthetic-private-report" not in str(result)
        assert "matchTokens" not in str(result)
    assert visit.raw == raw


def test_legacy_notification_fields_keep_identity_and_do_not_invent_sightings():
    visit = normalize_visit(load_fixture("postcard_complete.json"))
    event = build_legacy_feed_item_event(visit, **ACCOUNT)
    assert event["item_id"] == visit.item_id
    assert event["type"] == visit.kind
    assert event["feeder_id"] == "feeder-a"
    assert event["media_url"] == visit.medias[0].content_url
    assert event["thumbnail_url"] == visit.medias[0].thumbnail_url
    assert "sighting" not in event
    assert len(event["medias"]) == 3


async def test_detail_response_recovers_all_oversized_media_with_one_read():
    visit = normalize_visit(load_fixture("postcard_long_urls.json"))
    client = MagicMock()
    client.get_feed_item = AsyncMock(return_value=visit)
    client.identify_postcard = AsyncMock()
    client.collect_postcard = AsyncMock()
    client.share_medias = AsyncMock()
    event = build_postcard_event(visit, **ACCOUNT)
    result = await async_get_postcard_details(
        client, event["details"]["data"]["postcard_id"], **ACCOUNT
    )
    client.get_feed_item.assert_awaited_once_with(visit.item_id)
    client.identify_postcard.assert_not_called()
    client.collect_postcard.assert_not_called()
    client.share_medias.assert_not_called()
    assert result["medias_complete"] is True
    assert len(result["medias"]) == event["media_count"]
    assert result["medias"] == postcard_details(visit, **ACCOUNT)["medias"]


async def test_detail_response_rejects_wrong_card_identity():
    client = MagicMock(
        get_feed_item=AsyncMock(
            return_value=normalize_visit(load_fixture("postcard_complete.json"))
        )
    )
    with pytest.raises(ValueError, match="identity does not match"):
        await async_get_postcard_details(client, "different-card", **ACCOUNT)


@pytest.mark.parametrize(
    "builder", [build_postcard_event, build_legacy_feed_item_event]
)
def test_stable_delivery_identity_and_backfill_source_are_inside_the_byte_budget(
    builder,
):
    visit = normalize_visit(load_fixture("postcard_long_urls.json"))
    result = builder(
        visit, **ACCOUNT, event_id="birdbuddy:stable-id", source="backfill"
    )
    assert result["event_id"] == "birdbuddy:stable-id"
    assert result["source"] == "backfill"
    assert event_data_size(result) <= EVENT_DATA_BUDGET_BYTES
    assert result["details_required"] is True


@pytest.mark.parametrize(
    "context",
    [
        {"source": "guessed-history"},
        {"event_id": ""},
        {"event_id": 123},
        {"event_id": "x" * 31_000},
    ],
)
def test_invalid_or_unrepresentable_delivery_context_is_never_silently_rewritten(
    context,
):
    visit = normalize_visit(load_fixture("postcard_complete.json"))
    with pytest.raises(ValueError):
        build_postcard_event(visit, **ACCOUNT, **context)


@pytest.mark.parametrize("budget", [0, 30_001, 50])
def test_invalid_budget_or_unrepresentable_envelope_fails_explicitly(budget):
    visit = normalize_visit(load_fixture("postcard_complete.json"))
    with pytest.raises(ValueError, match="budget"):
        build_postcard_event(visit, **ACCOUNT, max_bytes=budget)
