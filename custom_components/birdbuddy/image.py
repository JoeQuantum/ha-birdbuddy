"""The Bird Buddy image entity."""

from birdbuddy.media import Media, is_media_expired
from homeassistant.components.image import (
    UNDEFINED,
    ImageEntity,
    Image,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LOGGER, RECENT_VISITOR_COUNT
from .coordinator import BirdBuddyDataUpdateCoordinator
from .device import BirdBuddyDevice
from .entity import BirdBuddyMixin
from .visitors import RecentVisitors


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Initialize config entry."""
    coordinator: BirdBuddyDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    feeders = coordinator.feeders.values()
    async_add_entities(
        BirdBuddyRecentVisitorImageEntity(hass, f, coordinator) for f in feeders
    )
    async_add_entities(
        BirdBuddyIndexedRecentVisitorImageEntity(hass, f, coordinator, index=i)
        for f in feeders
        for i in range(2, RECENT_VISITOR_COUNT + 1)
    )


class BirdBuddyRecentVisitorImageEntity(BirdBuddyMixin, ImageEntity):
    """The latest visitor image entity."""

    _attr_has_entity_name = True
    _attr_name = "Recent Visitor Image"

    _latest_media: Media | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        """Initialize the entity."""
        ImageEntity.__init__(self, hass)
        BirdBuddyMixin.__init__(self, feeder, coordinator)
        self._latest_media = None
        self._attr_unique_id = f"{self.feeder.id}-recent-image"

    def image(self) -> bytes | None:
        """Return the image bytes."""
        # See async_image()
        return None

    async def _async_load_image_from_url(self, url: str) -> Image | None:
        """
        Load an image by URL, ensuring compatibility with Home Assistant.

        This method overrides the parent implementation because cloudfront
        sometimes returns a `text/plain` content type for image data, which
        is incompatible with Home Assistant's requirement for `image/*`.
        To address this, the content type is explicitly set to `image/jpeg`.

        If there's an HTTP error, `fetch_url` will still raise the appropriate
        exception.
        """
        if response := await self._fetch_url(url):
            return Image(
                content=response.content,
                content_type="image/jpeg",
            )
        return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (
            (visitors := self.coordinator.visitors.get(self.feeder.id))
            and (media := visitors.latest_media)
        ):
            self._update_url(media)
        self.async_on_remove(
            self.coordinator.add_visitor_listener(
                self.feeder,
                self._on_recent_visitor,
            )
        )

    @callback
    def _on_recent_visitor(self, visitors: RecentVisitors) -> None:
        self._update_url(visitors.latest_media)
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        url = self._attr_image_url
        if url and url is not UNDEFINED and is_media_expired(url):
            self._attr_image_url = None
            self._attr_image_last_updated = None
            self._attr_entity_picture = None
        super()._handle_coordinator_update()

    def _update_url(self, media: Media) -> None:
        if (
            media
            and (url := media.content_url or media.thumbnail_url)
            and (created_at := media.created_at)
            and not is_media_expired(url)
        ):
            LOGGER.debug(
                "Updating latest image for %s: %s",
                self.feeder.name,
                url,
            )
            self._attr_image_url = url
            self._attr_image_last_updated = created_at
            self._attr_entity_picture = url
            self._cached_image = None
        elif (url := self.image_url) and url is not UNDEFINED and is_media_expired(url):
            # Clear it
            self._attr_image_url = None
            self._attr_image_last_updated = None
            self._attr_entity_picture = None


class BirdBuddyIndexedRecentVisitorImageEntity(BirdBuddyMixin, ImageEntity):
    """Carousel position N (2..RECENT_VISITOR_COUNT) for the recent-visitor feed.

    Position 1 (the latest) is owned by `BirdBuddyRecentVisitorImageEntity`.
    These indexed entities are disabled by default so users opt in to the
    carousel. They read from `RecentVisitors.recent`, which is rebuilt from
    the feed each poll — so signed URLs stay fresh without per-entity
    expiry handling.
    """

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        hass: HomeAssistant,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
        index: int,
    ) -> None:
        """Initialize the entity at carousel position `index` (2..N)."""
        ImageEntity.__init__(self, hass)
        BirdBuddyMixin.__init__(self, feeder, coordinator)
        self._index = index
        self._attr_unique_id = f"{self.feeder.id}-recent-image-{index}"
        self._attr_name = f"Recent Visitor Image {index}"

    def image(self) -> bytes | None:
        """Return the image bytes."""
        # See async_image()
        return None

    async def _async_load_image_from_url(self, url: str) -> Image | None:
        """Override content-type to image/jpeg to accept CloudFront's
        occasional `text/plain`; mirrors BirdBuddyRecentVisitorImageEntity."""
        if response := await self._fetch_url(url):
            return Image(
                content=response.content,
                content_type="image/jpeg",
            )
        return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.add_visitor_listener(
                self.feeder,
                self._on_recent_visitor,
            )
        )

    @callback
    def _on_recent_visitor(self, visitors: RecentVisitors) -> None:
        pos = self._index - 1
        recent = visitors.recent
        media = recent[pos].media if pos < len(recent) else None
        if media and (url := media.content_url or media.thumbnail_url):
            self._attr_image_url = url
            self._attr_image_last_updated = recent[pos].created_at
            self._attr_entity_picture = url
            self._cached_image = None
        else:
            self._attr_image_url = None
            self._attr_image_last_updated = None
            self._attr_entity_picture = None
        self.async_write_ha_state()
