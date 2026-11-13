"""Account-scoped Bird Buddy collections and fresh playback URLs."""

from datetime import datetime, timezone
from urllib.parse import urlsplit

from birdbuddy.media import Collection, Media, is_media_expired
from birdbuddy.exceptions import AuthenticationFailedError

from homeassistant.components.media_player.const import MediaClass, MediaType
from homeassistant.components.media_source.error import MediaSourceError, Unresolvable
from homeassistant.components.media_source.models import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .coordinator import BirdBuddyDataUpdateCoordinator


class BirdBuddyMediaSource(MediaSource):
    """Provide collections within the account selected in each browser URL."""

    name: str = "Bird Buddy"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize BirdBuddyMediaSource."""
        super().__init__(DOMAIN)
        self.hass = hass

    def _root_media_source(self) -> BrowseMediaSource:
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="",
            media_class=MediaClass.DIRECTORY,
            media_content_type="",
            title="Bird Buddy Media",
            can_play=False,
            can_expand=True,
            children=self._account_media_sources(),
            children_media_class=MediaClass.DIRECTORY,
        )

    @callback
    @classmethod
    def _parse_identifier(
        cls, identifier: str
    ) -> tuple[str | None, str | None, str | None]:
        data: list[str | None] = list(identifier.split("#", 2))
        data.extend([None] * (3 - len(data)))
        return data[0], data[1], data[2]

    def _get_account(
        self, config_id: str
    ) -> tuple[ConfigEntry, BirdBuddyDataUpdateCoordinator]:
        """Reject stale account links without selecting a different account."""
        entry = self.hass.config_entries.async_get_entry(config_id)
        coordinator = self.hass.data.get(DOMAIN, {}).get(config_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
            or coordinator is None
        ):
            raise MediaSourceError("This Bird Buddy account is not loaded")
        try:
            coordinator.verify_account_identity()
        except AuthenticationFailedError:
            entry.async_start_reauth(self.hass)
            raise MediaSourceError(
                "This Bird Buddy account needs reauthentication"
            ) from None
        return entry, coordinator

    def _check_loaded(
        self, config: ConfigEntry, coordinator: BirdBuddyDataUpdateCoordinator
    ) -> None:
        """Do not return data if the account unloaded during a remote read."""
        _, current = self._get_account(config.entry_id)
        if current is not coordinator:
            raise MediaSourceError("This Bird Buddy account was reloaded")

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Read media again at playback time, with one bounded expiry retry."""
        config_id, collection_id, media_id = self._parse_identifier(item.identifier)
        if not config_id or not collection_id or not media_id:
            raise Unresolvable("Incomplete Bird Buddy media identifier")
        config = None
        try:
            config, coordinator = self._get_account(config_id)
            for _ in range(2):
                medias = await coordinator.client.collection(collection_id)
                self._check_loaded(config, coordinator)
                media = medias.get(media_id)
                if media is None:
                    break
                if url := _playback_url(media):
                    return PlayMedia(url, _mime_type(media))
        except AuthenticationFailedError:
            if config is not None:
                config.async_start_reauth(self.hass)
            raise Unresolvable(
                "This Bird Buddy account needs reauthentication"
            ) from None
        except Exception:  # SDK and transport errors must not expose signed URLs
            raise Unresolvable("Unable to resolve Bird Buddy media") from None
        raise Unresolvable("Bird Buddy media is missing or expired")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse loaded accounts and their complete collection snapshots."""
        if not item.identifier:
            return self._build_media_configs()
        config_id, collection_id, _ = self._parse_identifier(item.identifier)
        if not config_id:
            raise MediaSourceError("Incomplete Bird Buddy account identifier")
        config = None
        try:
            config, coordinator = self._get_account(config_id)
            collections = await coordinator.client.refresh_collections()
            self._check_loaded(config, coordinator)
            if collection_id:
                collection = collections.get(collection_id)
                if collection is None:
                    raise MediaSourceError("This Bird Buddy collection is unavailable")
                return await self._build_media_collection_entries(
                    config, coordinator, collection
                )
            base = self._account_media_source(config)
            base.children = [
                self._build_media_collection(config, value)
                for value in collections.values()
            ]
            return base
        except AuthenticationFailedError:
            if config is not None:
                config.async_start_reauth(self.hass)
            raise MediaSourceError(
                "This Bird Buddy account needs reauthentication"
            ) from None
        except MediaSourceError:
            raise
        except Exception:  # no raw backend payload or private URL in HA errors
            raise MediaSourceError("Unable to browse Bird Buddy collections") from None

    def _account_media_source(self, config: ConfigEntry) -> BrowseMediaSource:
        _, coordinator = self._get_account(config.entry_id)
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=config.entry_id,
            media_class=MediaClass.DIRECTORY,
            media_content_type="",
            title=config.title,
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.DIRECTORY,
            thumbnail=(
                _usable_url(user.get("avatarUrl"))
                if (user := coordinator.client.user)
                else None
            ),
        )

    def _build_media_configs(self) -> BrowseMediaSource:
        """Build the root media source, omitting accounts that are not loaded."""
        return self._root_media_source()

    def _account_media_sources(self) -> list[BrowseMediaSource]:
        loaded = self.hass.data.get(DOMAIN, {})
        return [
            self._account_media_source(entry)
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
            and loaded.get(entry.entry_id) is not None
        ]

    @classmethod
    def _build_media_collection(
        cls, config: ConfigEntry, collection: Collection
    ) -> BrowseMediaSource:
        cover = collection.cover_media
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{config.entry_id}#{collection.collection_id}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.IMAGE,
            title=collection.bird_name or "Unknown species",
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.IMAGE,
            thumbnail=_usable_url(cover.get("thumbnailUrl")),
        )

    async def _build_media_collection_entries(
        self,
        config: ConfigEntry,
        coordinator: BirdBuddyDataUpdateCoordinator,
        collection: Collection,
    ) -> BrowseMediaSource:
        base = self._build_media_collection(config, collection)
        medias = await coordinator.client.collection(collection.collection_id)
        self._check_loaded(config, coordinator)
        base.children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{config.entry_id}#{collection.collection_id}#{media_id}",
                media_class=_media_class(media),
                media_content_type=_mime_type(media),
                title=_best_timedelta_title(_media_timestamp(media), dt_util.utcnow()),
                can_play=media.get("__typename") in {"MediaImage", "MediaVideo"},
                can_expand=False,
                thumbnail=_usable_url(media.get("thumbnailUrl")),
            )
            for media_id, media in medias.items()
        ]
        return base


async def async_get_media_source(hass: HomeAssistant) -> BirdBuddyMediaSource:
    """Set up media source."""
    return BirdBuddyMediaSource(hass)


def _usable_url(value: object) -> str | None:
    """Missing or malformed expiry is unknown; an explicit expiry is enforced."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        return None
    try:
        if is_media_expired(value):
            return None
    except (TypeError, ValueError, OverflowError):
        # Older client versions can raise on malformed Expires metadata.
        return value
    return value


def _playback_url(media: Media) -> str | None:
    if media.get("__typename") not in {"MediaImage", "MediaVideo"}:
        return None
    if url := _usable_url(media.get("contentUrl")):
        return url
    if _media_class(media) is MediaClass.IMAGE:
        return _usable_url(media.get("thumbnailUrl"))
    return None


def _media_class(media: Media) -> MediaClass:
    if media.get("__typename") == "MediaVideo":
        return MediaClass.VIDEO
    if media.get("__typename") == "MediaImage":
        return MediaClass.IMAGE
    return MediaClass.URL


def _mime_type(media: Media) -> str:
    if _media_class(media) is MediaClass.VIDEO:
        return "video/mp4"
    if _media_class(media) is MediaClass.IMAGE:
        return "image/jpeg"
    return "application/octet-stream"


def _media_timestamp(media: Media) -> datetime | None:
    value = media.get("createdAt")
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, datetime):
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    except (TypeError, ValueError, OverflowError):
        return None
    return None


def _best_timedelta_title(other: datetime | None, now: datetime) -> str:
    if other is None:
        return "Unknown capture time"
    other = other.astimezone(dt_util.DEFAULT_TIME_ZONE).replace(microsecond=0)
    if other > now:
        return other.strftime("%c")
    delta = now - other
    if delta.days < 1:
        return dt_util.get_age(other) + " ago"
    if delta.days < 7:
        return other.strftime("%a, %X")
    if delta.days < 365:
        return other.strftime("%c")
    return other.strftime("%x")
