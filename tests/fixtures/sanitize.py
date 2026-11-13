"""Deterministic, in-memory sanitation for curated public fixture excerpts.

Never write an unreviewed raw response into the repository. This helper replaces
known identifiers, names, credentials, contact/location fields and URLs; human
review remains necessary for free text and newly introduced fields.
"""

from __future__ import annotations

from typing import Any


class FixtureSanitizer:
    """Preserve selected API shapes while replacing identifying values."""

    def __init__(self) -> None:
        self._ids: dict[str, str] = {}
        self._urls: dict[str, str] = {}

    def sanitize(self, value: Any, *, key: str = "", parent_type: str = "") -> Any:
        """Return a new tree; use one instance to preserve cross-fixture identity."""
        if isinstance(value, dict):
            typename = value.get("__typename", parent_type)
            return {
                child_key: self.sanitize(child, key=child_key, parent_type=typename)
                for child_key, child in value.items()
            }
        if isinstance(value, list):
            return [
                self.sanitize(child, key=key, parent_type=parent_type)
                for child in value
            ]
        lower = key.lower()
        if lower in {"latitude", "longitude"} and value is not None:
            return 0.0
        if not isinstance(value, str):
            return value
        if lower in {
            "password",
            "accesstoken",
            "refreshtoken",
            "reporttoken",
            "authorization",
            "credential",
            "token",
        }:
            return "REDACTED"
        if lower.endswith("email"):
            return "fixture@example.invalid"
        if value.startswith(("https://", "http://", "wss://")):
            if value not in self._urls:
                self._urls[value] = (
                    f"https://example.invalid/asset-{len(self._urls) + 1:03}"
                )
            return self._urls[value]
        if lower == "id" or lower.endswith("id") or lower == "serialnumber":
            if value not in self._ids:
                self._ids[value] = f"fixture-id-{len(self._ids) + 1:03}"
            return self._ids[value]
        if lower in {
            "name",
            "ownername",
            "membername",
            "sharedbyname",
            "submittername",
        }:
            if key == "name" and parent_type.startswith("Species"):
                return value  # Public species labels are reference data.
            return "Fixture name"
        if lower in {
            "address",
            "city",
            "locationcity",
            "region",
            "postalcode",
            "accesslocation",
        }:
            return "Fixture location"
        return value
