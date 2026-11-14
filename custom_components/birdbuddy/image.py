"""Bird Buddy image entities with bounded, account-scoped media refresh."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import monotonic, time
from typing import Any
from urllib.parse import parse_qs, urlsplit
from weakref import WeakKeyDictionary

import aiohttp
from birdbuddy.media import Media, is_media_expired
from homeassistant.components.image import Image, ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LOGGER, RECENT_VISITOR_COUNT
from .coordinator import BirdBuddyDataUpdateCoordinator
from .platform import async_register_feeder_entities
from .device import BirdBuddyDevice
from .entity import BirdBuddyMixin
from .visitors import RecentVisitors

IMAGE_TIMEOUT = 10
REFRESH_TIMEOUT = 15
RETRY_SECONDS = 30
MAX_RETRY_SECONDS = 300
MAX_CACHE_SECONDS = 300
_MISSING_TIMESTAMP = object()


@dataclass
class _RefreshState:
    """Share one refresh budget across one account's feeder image slots."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_attempt: float = 0
    failures: int = 0


_REFRESH_STATES: WeakKeyDictionary[RecentVisitors, dict[str | None, _RefreshState]] = (
    WeakKeyDictionary()
)


def _value(media: object, name: str) -> Any:
    """Older client properties can raise when optional fields are absent."""
    try:
        return getattr(media, name, None)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None


def _timestamp(value: object) -> datetime | None:
    """Normalize timestamps without inventing a capture time for missing data."""
    try:
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, str):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except (TypeError, ValueError, OverflowError):
        return None


def _expired(url: str) -> bool:
    """Understand CloudFront/S3 expiry; absent or malformed metadata is unknown."""
    try:
        if is_media_expired(url):
            return True
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        pass
    try:
        query = parse_qs(urlsplit(url).query)
        if values := query.get("Expires"):
            return int(values[-1]) <= time()
        if (dates := query.get("X-Amz-Date")) and (
            durations := query.get("X-Amz-Expires")
        ):
            issued = datetime.strptime(dates[-1], "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            )
            return (
                issued + timedelta(seconds=int(durations[-1]))
            ).timestamp() <= time()
    except (TypeError, ValueError, OverflowError):
        pass
    return False


def _image_url(media: object) -> tuple[str | None, bool]:
    """Choose a usable still image, falling back from content to thumbnail."""
    video = _value(media, "is_video") is True
    if isinstance(media, Mapping):
        if media.get("__typename") not in {"MediaImage", "MediaVideo"}:
            return None, False
        video = video or media.get("__typename") == "MediaVideo"
    candidates = (
        (_value(media, "thumbnail_url"),)
        if video
        else (_value(media, "content_url"), _value(media, "thumbnail_url"))
    )
    expired = False
    for url in candidates:
        if not isinstance(url, str) or not url:
            continue
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if _expired(url):
            expired = True
            continue
        return url, expired
    return None, expired


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the latest image and optional carousel image slots."""
    coordinator: BirdBuddyDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_register_feeder_entities(
        entry,
        coordinator,
        async_add_entities,
        [
            lambda feeder: BirdBuddyRecentVisitorImageEntity(hass, feeder, coordinator),
            *[
                lambda feeder, index=index: BirdBuddyIndexedRecentVisitorImageEntity(
                    hass, feeder, coordinator, index=index
                )
                for index in range(2, RECENT_VISITOR_COUNT + 1)
            ],
        ],
    )


class _BirdBuddyVisitorImage(BirdBuddyMixin, ImageEntity):
    """Invalidate bytes and refresh only the existing read-only visitor surface.

    The refresh hook calls RecentVisitors.async_update for the canonical visit,
    never the coordinator poll or an identification/collection mutation.
    No last-known image is retained
    after a slot clears, expires or fails to download.
    """

    _latest_media: Media | None = None
    _visit_id: str | None = None
    _media_identity: object = None
    _generation = 0
    _image_lock: asyncio.Lock | None = None
    _active_request: asyncio.Task | None = None
    _removed = False
    _needs_refresh = False
    _download_failed = False
    _retry_at = 0.0
    _failures = 0
    _cached_at = 0.0
    _http_status: int | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        ImageEntity.__init__(self, hass)
        BirdBuddyMixin.__init__(self, feeder, coordinator)
        self._image_lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        """An absent, expired or failed image is unavailable, not the old visit."""
        url = self._attr_image_url
        return (
            super().available
            and not self._removed
            and not self._download_failed
            and isinstance(url, str)
            and bool(url)
            and not _expired(url)
        )

    def image(self) -> bytes | None:
        """Image retrieval is asynchronous."""
        return None

    def _clear_image(self, *, refresh: bool = False) -> None:
        self._generation += 1
        self._cached_image = None
        self._cached_at = 0
        self._attr_image_url = None
        self._attr_image_last_updated = None
        self._attr_entity_picture = None
        self._media_identity = None
        self._latest_media = None
        if not refresh:
            self._visit_id = None
        self._needs_refresh = refresh

    def _update_url(
        self,
        media: Media | None,
        captured_at: object = _MISSING_TIMESTAMP,
        visit_id: str | None = None,
    ) -> None:
        self._visit_id = visit_id
        if media is None:
            self._clear_image()
            return
        url, expired = _image_url(media)
        if url is None:
            self._clear_image(refresh=expired)
            return
        raw_time = captured_at
        if raw_time is _MISSING_TIMESTAMP:
            raw_time = (
                media.get("createdAt")
                if isinstance(media, Mapping)
                else _value(media, "created_at")
            )
        timestamp = _timestamp(raw_time)
        identity = (visit_id, _value(media, "id") or id(media), timestamp)
        if identity != self._media_identity or url != self._attr_image_url:
            self._generation += 1
            self._cached_image = None
            self._cached_at = 0
            self._retry_at = 0
            self._failures = 0
            self._download_failed = False
        self._latest_media = media
        self._media_identity = identity
        self._attr_image_url = url
        self._attr_image_last_updated = timestamp
        self._attr_entity_picture = url
        self._needs_refresh = False

    def _update_from_visitors(self, visitors: RecentVisitors) -> None:
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._removed = False
        if visitors := self.coordinator.visitors.get(self.feeder.id):
            self._update_from_visitors(visitors)
        self.async_on_remove(
            self.coordinator.add_visitor_listener(self.feeder, self._on_recent_visitor)
        )

    @callback
    def _on_recent_visitor(self, visitors: RecentVisitors) -> None:
        self._update_from_visitors(visitors)
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        url = self._attr_image_url
        if isinstance(url, str) and url and _expired(url):
            self._clear_image(refresh=True)
        super()._handle_coordinator_update()

    async def _refresh_url(self) -> None:
        visitors = self.coordinator.visitors.get(self.feeder.id)
        if visitors is None:
            self._clear_image()
            return
        states = _REFRESH_STATES.setdefault(visitors, {})
        state = states.setdefault(self._visit_id, _RefreshState())
        active_ids = {visit.visit_id for visit in visitors.recent}
        for key in states.keys() - active_ids - {self._visit_id, None}:
            del states[key]
        async with state.lock:
            if monotonic() >= state.next_attempt:
                state.next_attempt = monotonic() + RETRY_SECONDS
                try:
                    async with asyncio.timeout(REFRESH_TIMEOUT):
                        if self._visit_id:
                            await visitors.async_update(visit_id=self._visit_id)
                        else:
                            await visitors.async_update(
                                sole_feeder=len(self.coordinator.feeders) == 1
                            )
                except Exception as exc:  # cancellation deliberately propagates
                    state.failures += 1
                    state.next_attempt = monotonic() + min(
                        MAX_RETRY_SECONDS, RETRY_SECONDS * 2 ** min(state.failures, 4)
                    )
                    LOGGER.debug(
                        "Visitor image refresh failed (%s)", type(exc).__name__
                    )
                else:
                    state.failures = 0
        if not self._removed:
            self._update_from_visitors(visitors)

    async def _async_load_image_from_url(self, url: str) -> Image | None:
        """Fetch without core HTTPX error logs that include signed URLs."""
        self._http_status = None
        try:
            async with asyncio.timeout(IMAGE_TIMEOUT):
                session = async_get_clientsession(self.hass)
                async with session.get(url) as response:
                    self._http_status = response.status
                    response.raise_for_status()
                    content = await response.read()
                    content_type = (
                        response.headers.get("Content-Type", "")
                        .split(";")[0]
                        .strip()
                        .lower()
                    )
        except (aiohttp.ClientError, TimeoutError) as exc:
            LOGGER.debug("Visitor image download failed (%s)", type(exc).__name__)
            return None
        if not content:
            return None
        if not content_type or content_type in {
            "text/plain",
            "application/octet-stream",
        }:
            content_type = "image/jpeg"
        if not content_type.startswith("image/"):
            return None
        return Image(content=content, content_type=content_type)

    async def async_image(self) -> bytes | None:
        """Serialize requests and discard results from an obsolete visit."""
        if self._image_lock is None:
            self._image_lock = asyncio.Lock()
        async with self._image_lock:
            if self._removed:
                return None
            self._active_request = asyncio.current_task()
            try:
                return await self._image_for_current_visit()
            finally:
                self._active_request = None
                if not self._removed and self.hass and self.entity_id:
                    self.async_write_ha_state()

    async def _image_for_current_visit(self) -> bytes | None:
        url = self._attr_image_url
        if isinstance(url, str) and url and _expired(url):
            self._clear_image(refresh=True)
        if self._needs_refresh:
            await self._refresh_url()
        for attempt in range(2):
            url = self._attr_image_url
            if self._removed or not isinstance(url, str) or not url:
                self._cached_image = None
                return None
            if self._cached_image and monotonic() - self._cached_at < MAX_CACHE_SECONDS:
                return self._cached_image.content
            self._cached_image = None
            if monotonic() < self._retry_at:
                return None
            generation = self._generation
            image = await self._async_load_image_from_url(url)
            if self._removed or generation != self._generation:
                return None
            if _expired(url):
                self._clear_image(refresh=True)
                return None
            if image is not None:
                self._cached_image = image
                self._cached_at = monotonic()
                self._download_failed = False
                self._failures = 0
                self._attr_content_type = image.content_type
                return image.content
            self._download_failed = True
            self._failures += 1
            self._retry_at = monotonic() + min(
                MAX_RETRY_SECONDS, RETRY_SECONDS * 2 ** min(self._failures - 1, 4)
            )
            if attempt == 0 and self._http_status in {401, 403}:
                failures, retry_at = self._failures, self._retry_at
                self._clear_image(refresh=True)
                await self._refresh_url()
                if self._attr_image_url != url:
                    continue
                self._download_failed = True
                self._failures, self._retry_at = failures, retry_at
            return None
        return None

    async def async_will_remove_from_hass(self) -> None:
        self._removed = True
        self._clear_image()
        task = self._active_request
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await super().async_will_remove_from_hass()


class BirdBuddyRecentVisitorImageEntity(_BirdBuddyVisitorImage):
    """The most recent visitor image."""

    _attr_has_entity_name = True
    _attr_name = "Recent Visitor Image"

    def __init__(
        self,
        hass: HomeAssistant,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(hass, feeder, coordinator)
        self._attr_unique_id = f"{self.feeder.id}-recent-image"

    def _update_from_visitors(self, visitors: RecentVisitors) -> None:
        if recent := getattr(visitors, "recent", []):
            visit = recent[0]
            self._update_url(visit.media, visit.created_at, visit.visit_id)
        else:
            self._update_url(visitors.latest_media)


class BirdBuddyIndexedRecentVisitorImageEntity(_BirdBuddyVisitorImage):
    """Optional carousel image; shares the latest image's cache and expiry rules."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        hass: HomeAssistant,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
        index: int,
    ) -> None:
        super().__init__(hass, feeder, coordinator)
        self._index = index
        self._attr_unique_id = f"{self.feeder.id}-recent-image-{index}"
        self._attr_name = f"Recent Visitor Image {index}"

    def _update_from_visitors(self, visitors: RecentVisitors) -> None:
        position = self._index - 1
        if position < len(visitors.recent):
            visit = visitors.recent[position]
            self._update_url(visit.media, visit.created_at, visit.visit_id)
        else:
            self._update_url(None)
