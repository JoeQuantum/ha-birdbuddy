"""Validate collection intent before any inference, collection or sharing."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class UnsupportedPostcardPolicy(ValueError):
    """A legacy policy has no validated equivalent in the current API."""


@dataclass(frozen=True)
class PostcardCollectionRequest:
    """Validated collection intent; account routing is a separate prerequisite."""

    postcard_id: str
    selection_policy: str
    share: bool
    identify: bool
    config_entry_id: str | None
    account_id: str | None
    device_id: str | None
    feeder_id: str | None
    legacy_aliases: tuple[str, ...]


_LEGACY_POLICY_FIELDS = {
    "strategy",
    "best_guess_confidence",
    "confidence_threshold",
    "sighting",
    "media_species_items",
    "mediaSpeciesItems",
    "species_ids",
}
_TARGET_FIELDS = ("config_entry_id", "account_id", "device_id", "feeder_id")
_COLLECTION_FIELDS = {
    "postcard_id",
    "postcard",
    "selection_policy",
    "share",
    "share_media",
    "identify",
    *_TARGET_FIELDS,
}


def _identifier(value: Any, name: str) -> str:
    """Require an explicit, nonempty ID without rewriting an opaque value."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a nonempty ID without surrounding whitespace")
    return value


def validate_collection_request(data: Mapping[str, Any]) -> PostcardCollectionRequest:
    """Reject unsupported semantics rather than silently changing old actions.

    Presence matters: even an explicit legacy default or null must be removed
    deliberately. This function has no API or Home Assistant side effects.
    """
    if unsupported := _LEGACY_POLICY_FIELDS.intersection(data):
        fields = ", ".join(sorted(unsupported))
        raise UnsupportedPostcardPolicy(
            f"Unsupported postcard fields: {fields}. Remove legacy policies and "
            "sighting data, then explicitly select selection_policy: server. "
            "This uses Bird Buddy's current selection and does not preserve a "
            "recognized-only, best-guess, mystery, or confidence threshold rule."
        )
    if set(data).difference(_COLLECTION_FIELDS):
        raise ValueError("Unsupported postcard action field; check the service schema")
    if data.get("selection_policy") != "server":
        raise UnsupportedPostcardPolicy(
            "Explicit selection_policy: server is required. Review Bird Buddy's "
            "current selection policy before migrating this action."
        )

    aliases: list[str] = []
    postcard_id = None
    if "postcard_id" in data:
        postcard_id = _identifier(data["postcard_id"], "postcard_id")
    if "postcard" in data:
        envelope = data["postcard"]
        if not isinstance(envelope, Mapping):
            raise ValueError("The postcard alias must be an object containing id")
        alias_id = _identifier(envelope.get("id"), "postcard.id")
        if postcard_id is not None and postcard_id != alias_id:
            raise ValueError("postcard_id and postcard.id conflict")
        postcard_id = alias_id
        aliases.append("postcard")
    if postcard_id is None:
        raise ValueError("postcard_id is required")

    for field in ("share", "share_media", "identify"):
        if field in data and type(data[field]) is not bool:
            raise ValueError(f"{field} must be a boolean")
    if (
        "share" in data
        and "share_media" in data
        and data["share"] != data["share_media"]
    ):
        raise ValueError("share and share_media conflict")
    if "share_media" in data:
        aliases.append("share_media")
    targets = {
        field: _identifier(data[field], field) if field in data else None
        for field in _TARGET_FIELDS
    }
    return PostcardCollectionRequest(
        postcard_id=postcard_id,
        selection_policy="server",
        share=data.get("share", data.get("share_media", False)),
        identify=data.get("identify", False),
        config_entry_id=targets["config_entry_id"],
        account_id=targets["account_id"],
        device_id=targets["device_id"],
        feeder_id=targets["feeder_id"],
        legacy_aliases=tuple(aliases),
    )
