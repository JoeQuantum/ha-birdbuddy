"""Media browsing and playback remain bounded to one loaded account."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from birdbuddy.media import Collection, Media
from birdbuddy.user import BirdBuddyUser
from homeassistant.components.media_player.const import MediaClass
from homeassistant.components.media_source.error import MediaSourceError, Unresolvable
from homeassistant.components.media_source.models import MediaSourceItem
from homeassistant.config_entries import ConfigEntryState

from custom_components.birdbuddy.const import DOMAIN
from custom_components.birdbuddy.media_source import BirdBuddyMediaSource
from tests.ha_compat import common as ha_test_common

URL = "https://cdn.example.invalid/image.jpg?Expires=99999999999&Signature=private"
EXPIRED = "https://cdn.example.invalid/old.jpg?Expires=1&Signature=private"


def _media(url: str | None = URL, *, kind="MediaImage", **changes):
    return Media(
        {
            "id": "media-shared-id",
            "__typename": kind,
            "createdAt": "2026-01-01T12:00:00Z",
            "contentUrl": url,
            "thumbnailUrl": url,
            **changes,
        }
    )


def _collection(name="Robin"):
    return Collection(
        {
            "id": "collection-shared-id",
            "__typename": "CollectionBird",
            "species": {"id": "s1", "name": name},
            "visitsAllTime": 1,
            "coverCollectionMedia": {
                "id": "wrapper",
                "feeder": {"id": "shared-feeder"},
                "feederName": "Backyard",
                "media": _media().data,
            },
        }
    )


def _account(hass, account_id="account-a", *, loaded=True, url=URL):
    entry = ha_test_common.MockConfigEntry(
        domain=DOMAIN,
        unique_id=account_id,
        title=account_id,
        data={},
        state=ConfigEntryState.LOADED if loaded else ConfigEntryState.NOT_LOADED,
    )
    entry.add_to_hass(hass)
    client = SimpleNamespace(
        user=BirdBuddyUser({"id": account_id}),
        refresh_collections=AsyncMock(
            return_value={"collection-shared-id": _collection()}
        ),
        collection=AsyncMock(return_value={"media-shared-id": _media(url)}),
    )
    coordinator = SimpleNamespace(client=client, verify_account_identity=MagicMock())
    if loaded:
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    return entry, coordinator


def _item(hass, entry=None, *, collection=False, media=False):
    identifier = entry.entry_id if entry else ""
    if collection or media:
        identifier += "#collection-shared-id"
    if media:
        identifier += "#media-shared-id"
    return MediaSourceItem(hass, DOMAIN, identifier, None)


async def test_root_omits_unloaded_accounts(hass):
    source = BirdBuddyMediaSource(hass)
    assert not (await source.async_browse_media(_item(hass))).children
    loaded, _ = _account(hass)
    _account(hass, "unloaded", loaded=False)
    result = await source.async_browse_media(_item(hass))
    assert result.children is not None
    assert [child.identifier for child in result.children] == [loaded.entry_id]


@pytest.mark.parametrize("missing_entry", [False, True])
async def test_stale_account_identifier_is_safe(hass, missing_entry):
    source = BirdBuddyMediaSource(hass)
    if missing_entry:
        item = MediaSourceItem(hass, DOMAIN, "missing#collection#media", None)
    else:
        entry, _ = _account(hass, loaded=False)
        item = _item(hass, entry, media=True)
    with pytest.raises(MediaSourceError):
        await source.async_browse_media(item)
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(item)


async def test_matching_ids_stay_in_selected_account(hass):
    source = BirdBuddyMediaSource(hass)
    first, one = _account(hass, "account-a", url=URL)
    second_url = "https://cdn.example.invalid/account-b.jpg"
    second, two = _account(hass, "account-b", url=second_url)
    resolved = await source.async_resolve_media(_item(hass, second, media=True))
    assert resolved.url == second_url
    two.client.collection.assert_awaited_once_with("collection-shared-id")
    one.client.collection.assert_not_awaited()
    browsed = await source.async_browse_media(_item(hass, first))
    assert browsed.children is not None
    assert browsed.children[0].identifier == first.entry_id + "#collection-shared-id"
    one.client.refresh_collections.assert_awaited_once()
    two.client.refresh_collections.assert_not_awaited()


async def test_missing_collection_and_media_use_domain_errors(hass):
    source = BirdBuddyMediaSource(hass)
    entry, coordinator = _account(hass)
    coordinator.client.refresh_collections.return_value = {}
    coordinator.client.collection.return_value = {}
    with pytest.raises(MediaSourceError):
        await source.async_browse_media(_item(hass, entry, collection=True))
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(_item(hass, entry, media=True))
    coordinator.client.collection.assert_awaited_once()


@pytest.mark.parametrize("resolve", [False, True])
async def test_unload_during_read_does_not_return_data(hass, resolve):
    source = BirdBuddyMediaSource(hass)
    entry, coordinator = _account(hass)

    async def unload(*_args):
        hass.data[DOMAIN].pop(entry.entry_id)
        return {"media-shared-id": _media()}

    if resolve:
        coordinator.client.collection.side_effect = unload
        with pytest.raises(Unresolvable):
            await source.async_resolve_media(_item(hass, entry, media=True))
    else:
        coordinator.client.refresh_collections.side_effect = unload
        with pytest.raises(MediaSourceError):
            await source.async_browse_media(_item(hass, entry))


async def test_expired_playback_url_reloads_once(hass):
    source = BirdBuddyMediaSource(hass)
    entry, coordinator = _account(hass)
    coordinator.client.collection.side_effect = [
        {"media-shared-id": _media(EXPIRED)},
        {"media-shared-id": _media(URL)},
    ]
    resolved = await source.async_resolve_media(_item(hass, entry, media=True))
    assert resolved.url == URL
    assert coordinator.client.collection.await_count == 2


async def test_persistently_expired_url_is_bounded(hass):
    source = BirdBuddyMediaSource(hass)
    entry, coordinator = _account(hass, url=EXPIRED)
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(_item(hass, entry, media=True))
    assert coordinator.client.collection.await_count == 2


@pytest.mark.parametrize("suffix", ["", "?Expires=invalid"])
async def test_missing_expiry_does_not_block_playback(hass, suffix):
    url = "https://cdn.example.invalid/image.jpg" + suffix
    source = BirdBuddyMediaSource(hass)
    entry, _ = _account(hass, url=url)
    assert (await source.async_resolve_media(_item(hass, entry, media=True))).url == url


async def test_images_and_videos_have_playable_leaf_entries(hass):
    source = BirdBuddyMediaSource(hass)
    entry, coordinator = _account(hass)
    coordinator.client.collection.return_value = {
        "image": _media(),
        "video": _media("https://cdn.example.invalid/video.mp4", kind="MediaVideo"),
    }
    result = await source.async_browse_media(_item(hass, entry, collection=True))
    assert result.children is not None
    assert [child.media_class for child in result.children] == [
        MediaClass.IMAGE,
        MediaClass.VIDEO,
    ]
    assert [child.media_content_type for child in result.children] == [
        "image/jpeg",
        "video/mp4",
    ]
    assert all(child.can_play and not child.can_expand for child in result.children)


async def test_video_playback_never_returns_thumbnail(hass):
    source = BirdBuddyMediaSource(hass)
    entry, coordinator = _account(hass)
    coordinator.client.collection.return_value = {
        "media-shared-id": _media(None, kind="MediaVideo", thumbnailUrl=URL)
    }
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(_item(hass, entry, media=True))


async def test_expired_image_can_use_current_thumbnail(hass):
    source = BirdBuddyMediaSource(hass)
    entry, coordinator = _account(hass)
    coordinator.client.collection.return_value = {
        "media-shared-id": _media(EXPIRED, thumbnailUrl=URL)
    }
    assert (await source.async_resolve_media(_item(hass, entry, media=True))).url == URL
    coordinator.client.collection.assert_awaited_once()


@pytest.mark.parametrize("stamp", [None, "invalid", 12345])
async def test_unknown_capture_time_is_not_fabricated(hass, stamp):
    source = BirdBuddyMediaSource(hass)
    entry, coordinator = _account(hass)
    coordinator.client.collection.return_value = {"media": _media(createdAt=stamp)}
    result = await source.async_browse_media(_item(hass, entry, collection=True))
    assert result.children is not None
    assert result.children[0].title == "Unknown capture time"


@pytest.mark.parametrize("resolve", [False, True])
async def test_backend_errors_hide_signed_urls(hass, resolve, caplog):
    source = BirdBuddyMediaSource(hass)
    entry, coordinator = _account(hass)
    method = (
        coordinator.client.collection
        if resolve
        else coordinator.client.refresh_collections
    )
    method.side_effect = RuntimeError("Remote error " + URL)
    with pytest.raises(MediaSourceError) as caught:
        if resolve:
            await source.async_resolve_media(_item(hass, entry, media=True))
        else:
            await source.async_browse_media(_item(hass, entry))
    assert "Signature" not in str(caught.value)
    assert "Signature" not in caplog.text


async def test_playback_cancellation_propagates(hass):
    source = BirdBuddyMediaSource(hass)
    entry, coordinator = _account(hass)
    coordinator.client.collection.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await source.async_resolve_media(_item(hass, entry, media=True))


async def test_not_loaded_entry_with_stale_runtime_data_is_rejected(hass):
    source = BirdBuddyMediaSource(hass)
    entry, coordinator = _account(hass, loaded=False)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    assert not (await source.async_browse_media(_item(hass))).children
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(_item(hass, entry, media=True))
    coordinator.client.collection.assert_not_awaited()


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "file:///tmp/image.jpg", "https://[invalid"]
)
async def test_unsupported_or_malformed_urls_are_unresolvable(hass, url):
    source = BirdBuddyMediaSource(hass)
    entry, _ = _account(hass, url=url)
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(_item(hass, entry, media=True))


async def test_unknown_media_kind_is_retained_but_not_playable(hass):
    source = BirdBuddyMediaSource(hass)
    entry, coordinator = _account(hass)
    coordinator.client.collection.return_value = {
        "media-shared-id": _media(kind="MediaAudio")
    }
    result = await source.async_browse_media(_item(hass, entry, collection=True))
    assert result.children is not None
    assert len(result.children) == 1
    assert result.children[0].media_class is MediaClass.URL
    assert not result.children[0].can_play
    assert result.children[0].media_content_type == "application/octet-stream"
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(_item(hass, entry, media=True))
