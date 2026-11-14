"""Versioned, bounded postcard events and complete read-only detail responses."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.json import json_bytes

if TYPE_CHECKING:
    from birdbuddy.client import BirdBuddy
    from birdbuddy.visits import Visit, VisitMedia

EVENT_SCHEMA_VERSION = 1
EVENT_DATA_BUDGET_BYTES = 30_000
POSTCARD_DETAIL_SERVICE = "birdbuddy.get_postcard_details"


def _media_data(media: VisitMedia | None) -> dict[str, Any] | None:
    if media is None:
        return None
    return {
        "id": media.id,
        "kind": media.kind,
        "created_at": media.created_at.isoformat() if media.created_at else None,
        "content_url": media.content_url,
        "thumbnail_url": media.thumbnail_url,
    }


def postcard_details(
    visit: Visit, *, config_entry_id: str, account_id: str
) -> dict[str, Any]:
    """Return every selected media/species field, without raw reports or tokens."""
    for value in (visit.item_id, config_entry_id, account_id):
        if not isinstance(value, str) or not value:
            raise ValueError("Postcard, config entry and account IDs are required")
    representative = visit.representative_image or next(
        (media for media in visit.medias if media.thumbnail_url),
        next(iter(visit.medias), None),
    )
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "postcard_id": visit.item_id,
        "config_entry_id": config_entry_id,
        "account_id": account_id,
        "feeder_id": visit.feeder_id,
        "attribution": visit.attribution,
        "created_at": visit.created_at.isoformat() if visit.created_at else None,
        "kind": visit.kind,
        "species": [
            {"id": species.id, "name": species.name, "icon_url": species.icon_url}
            for species in visit.species
        ],
        "medias": [_media_data(media) for media in visit.medias],
        "media": _media_data(representative),
        "preview_url": visit.preview_url,
        "media_count": len(visit.medias),
        "species_count": len(visit.species),
        "medias_complete": True,
        "media_complete": True,
        "species_complete": True,
        "details_required": False,
        "omitted_fields": [],
        "details": {
            "available": True,
            "service": POSTCARD_DETAIL_SERVICE,
            "data": {"postcard_id": visit.item_id, "config_entry_id": config_entry_id},
        },
    }


def event_data_size(payload: dict[str, Any]) -> int:
    """Measure the UTF-8 encoding used by HA recorder, including signed URLs."""
    return len(json_bytes(payload))


def _delivery_context(
    payload: dict[str, Any], event_id: str | None, source: str
) -> None:
    if source not in {"live", "backfill"}:
        raise ValueError("Event source must be live or backfill")
    if event_id is not None and (not isinstance(event_id, str) or not event_id):
        raise ValueError("event_id must be a nonempty stable ID")
    payload.update(event_id=event_id, source=source)


def _bounded(payload: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    """Compact a fresh DTO, declaring every omission and retaining detail routing."""
    if not 0 < max_bytes <= EVENT_DATA_BUDGET_BYTES:
        raise ValueError("Event byte budget must be between 1 and 30000")
    if event_data_size(payload) <= max_bytes:
        return payload

    payload["details_required"] = True
    payload["medias"] = []
    payload["medias_complete"] = False
    payload["omitted_fields"].append("medias")
    if event_data_size(payload) <= max_bytes:
        return payload

    # A single signed URL can exceed the entire recorder limit. Keep its ID
    # and media kind, and expose the original URL through the detail response.
    if payload["media"] is not None:
        payload["media"]["content_url"] = None
        payload["media"]["thumbnail_url"] = None
        payload["media_complete"] = False
        payload["omitted_fields"].extend(["media.content_url", "media.thumbnail_url"])
    payload["preview_url"] = None
    payload["omitted_fields"].append("preview_url")
    for field in ("media_url", "thumbnail_url"):
        if field in payload:
            payload[field] = None
            payload["omitted_fields"].append(field)
    if event_data_size(payload) <= max_bytes:
        return payload

    for species in payload["species"]:
        species["icon_url"] = None
    payload["species_complete"] = False
    payload["omitted_fields"].append("species.icon_url")
    if event_data_size(payload) <= max_bytes:
        return payload

    payload["species"] = []
    payload["omitted_fields"].append("species")
    payload["media"] = None
    payload["media_complete"] = False
    payload["omitted_fields"].append("media")
    if event_data_size(payload) > max_bytes:
        # Never truncate opaque IDs or remove the lookup route to make an event
        # fit: the caller must report the malformed/unrepresentable identity.
        raise ValueError("Postcard event identity exceeds the event byte budget")
    return payload


def build_postcard_event(
    visit: Visit,
    *,
    config_entry_id: str,
    account_id: str,
    event_id: str | None = None,
    source: str = "live",
    max_bytes: int = EVENT_DATA_BUDGET_BYTES,
) -> dict[str, Any]:
    """Build schema v1 for birdbuddy_new_postcard without changing canonical data."""
    payload = postcard_details(
        visit, config_entry_id=config_entry_id, account_id=account_id
    )
    _delivery_context(payload, event_id, source)
    return _bounded(payload, max_bytes)


def build_legacy_feed_item_event(
    visit: Visit,
    *,
    config_entry_id: str,
    account_id: str,
    event_id: str | None = None,
    source: str = "live",
    max_bytes: int = EVENT_DATA_BUDGET_BYTES,
) -> dict[str, Any]:
    """Retain the notification event's six fields plus the canonical contract.

    This adapter does not recreate the obsolete postcard-sighting event.
    A video's content URL is never substituted for the old image media_url.
    """
    payload = postcard_details(
        visit, config_entry_id=config_entry_id, account_id=account_id
    )
    _delivery_context(payload, event_id, source)
    image = visit.representative_image
    payload.update(
        {
            "item_id": visit.item_id,
            "type": visit.kind,
            "media_url": image.content_url if image else None,
            "thumbnail_url": payload["media"]["thumbnail_url"]
            if payload["media"]
            else None,
        }
    )
    return _bounded(payload, max_bytes)


async def async_get_postcard_details(
    client: BirdBuddy, postcard_id: str, *, config_entry_id: str, account_id: str
) -> dict[str, Any]:
    """Read all selected assets through an already resolved account's client.

    The service handler must resolve explicit account/device targets first.
    This makes one read; it never invokes inference, collection or sharing.
    """
    if not isinstance(postcard_id, str) or not postcard_id:
        raise ValueError("postcard_id is required")
    visit = await client.get_feed_item(postcard_id)
    if visit.item_id != postcard_id:
        raise ValueError("Postcard response identity does not match the request")
    return postcard_details(
        visit, config_entry_id=config_entry_id, account_id=account_id
    )
