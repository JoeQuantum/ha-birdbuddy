"""Helpers for managing recent visitors."""

from dataclasses import dataclass
from typing import TypeVar
from collections.abc import Callable

from birdbuddy.birds import Species
from birdbuddy.client import BirdBuddy
from birdbuddy.feed import FeedNodeType
from birdbuddy.feeder import Feeder
from birdbuddy.media import Media, is_media_expired
from birdbuddy.sightings import PostcardSighting

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CALLBACK_TYPE

from .const import EVENT_NEW_POSTCARD_SIGHTING, LOGGER, RECENT_VISITOR_COUNT
from .util import _find_media

_RecentVisitors = TypeVar("_RecentVisitors", bound="RecentVisitors")
type VisitorCallback = Callable[[_RecentVisitors], None]


@dataclass(frozen=True)
class RecentVisitor:
    """One entry in the recent-visitor carousel.

    `media` is always present (entries without media are filtered out
    upstream). `species` may be `None` for unrecognized sightings.
    `created_at` is whatever pybirdbuddy's `Media.created_at` returns
    (typically a `datetime`).
    """

    media: Media
    species: Species | None
    created_at: object


class RecentVisitors:
    """Class to manage recent visitors to this Feeder."""

    def __init__(
        self,
        feeder: Feeder,
        client: BirdBuddy,
        hass: HomeAssistant,
        *,
        count: int = RECENT_VISITOR_COUNT,
        dedupe_by_species: bool = False,
    ) -> None:
        """Initialize the recent visitors manager."""
        self.hass = hass
        self.client = client
        self.feeder = feeder
        self.dedupe_by_species = dedupe_by_species
        self._count = count
        self._listeners: set[VisitorCallback] = set()
        self._disposable: Callable[[], None] | None = None
        self._recent: list[RecentVisitor] = []
        # Species surfaced when the feed has no matching items but the
        # collections endpoint at least knows the most recent species.
        # No media is available via this path.
        self._fallback_species: Species | None = None

    @property
    def recent(self) -> list[RecentVisitor]:
        """Return the most recent N visitors (head is newest)."""
        return self._recent

    @property
    def latest_media(self) -> Media | None:
        """Return the most recent visitor's media (head of `recent`)."""
        return self._recent[0].media if self._recent else None

    @property
    def latest_species(self) -> Species | None:
        """Return the most recent visitor's species.

        Falls back to the collections-only species when the feed had no
        matching items on the last poll.
        """
        if self._recent:
            return self._recent[0].species
        return self._fallback_species

    def register_callback(self, listener: VisitorCallback) -> CALLBACK_TYPE:
        """Register a callback to be called when a new visitor is detected."""
        if not self._listeners:
            self._disposable = self._start()
        if (media := self.latest_media) and not is_media_expired(
            media.content_url or media.thumbnail_url
        ):
            listener(self)
        self._listeners.add(listener)
        return lambda: self.unregister_callback(listener)

    def unregister_callback(self, listener: VisitorCallback) -> None:
        """Unregister a callback."""
        self._listeners.remove(listener)
        if not self._listeners:
            self._stop()

    def _stop(self) -> None:
        """Stop listening for new postcards."""
        if self._disposable:
            self._disposable()
            self._disposable = None
        LOGGER.info("Stopped listening for new visitors to feeder %s", self.feeder.name)

    def _start(self) -> Callable[[], None]:
        """Start listening for new postcards."""

        @callback
        def filter_my_postcards(event: Event) -> bool:
            data = event if callable(getattr(event, "get", None)) else event.data
            return self.feeder.id == (
                data.get("sighting", {}).get("feeder", {}).get("id")
            )

        LOGGER.info("Listening for new visitors to feeder %s", self.feeder.name)
        self.hass.add_job(self._update_latest_visitor)
        return self.hass.bus.async_listen(
            EVENT_NEW_POSTCARD_SIGHTING,
            self._on_new_postcard,
            event_filter=filter_my_postcards,
        )

    async def async_update(self) -> None:
        """Refresh the recent-visitor list from the feed (poll-driven).

        Called by the coordinator on every poll. This is the *only* refresh
        path that works for feeders without auto-ID: their visits arrive as
        postcards that can't be converted to sightings, so the event-driven
        path (`_on_new_postcard`) never fires for them. See issue #7.
        """
        await self._update_latest_visitor()

    async def _update_latest_visitor(self) -> None:
        feed = await self.client.feed()

        items = feed.filter(
            of_type=[
                FeedNodeType.SpeciesSighting,
                FeedNodeType.SpeciesUnlocked,
                FeedNodeType.CollectedPostcard,
                # Feeders without auto-ID only ever produce mystery visitors;
                # without these the recent-visitor list is always empty (#7).
                FeedNodeType.MysteryVisitorNotRecognized,
                FeedNodeType.MysteryVisitorResolved,
            ],
        )

        my_items = _find_media(self.feeder.id, items)

        # Rebuild the recent list from the feed each poll. Caching-and-appending
        # would let stale signed CloudFront URLs linger in the list past their
        # TTL; rebuilding gives every entry a fresh URL for free and is
        # restart-safe.
        sorted_items = sorted(my_items, key=lambda x: x.created_at, reverse=True)
        recent: list[RecentVisitor] = []
        seen_species: set[str] = set()
        for item in sorted_items:
            media = Media(item["media"])
            species = next(
                (Species(s) for s in item.get("species", [])),
                None,
            )
            if self.dedupe_by_species and species and species.name in seen_species:
                continue
            if species:
                seen_species.add(species.name)
            recent.append(
                RecentVisitor(
                    media=media,
                    species=species,
                    created_at=media.created_at,
                )
            )
            if len(recent) >= self._count:
                break

        self._recent = recent

        if recent:
            # Real entries available; the species-only fallback is stale.
            self._fallback_species = None
            LOGGER.debug(
                "Setting %d recent visitor(s) on %s from feed; latest: %s, %s: %s",
                len(recent),
                self.feeder.name,
                (
                    recent[0].species.name
                    if recent[0].species
                    else "Unknown species"
                ),
                recent[0].created_at,
                recent[0].media.content_url,
            )
        else:
            # No matching items in the feed — surface at least a species
            # from the collections endpoint. No media is available here.
            c = await self.client.refresh_collections()
            c = [c for c in c.values() if c.feeder_name == self.feeder.name]
            if c := max(c, default=None, key=(lambda x: x.last_visit)):
                self._fallback_species = c.species
                LOGGER.debug(
                    "Setting fallback recent species on %s from collection: %s",
                    self.feeder.name,
                    c.species.name,
                )

        self._notify_listeners()

    def _notify_listeners(self) -> None:
        """Notify listeners of the latest visitor."""
        for listener in self._listeners:
            listener(self)

    async def _on_new_postcard(self, event: Event | None = None) -> None:
        """Handle a new postcard sighting."""
        postcard = PostcardSighting(event.data["sighting"])

        assert postcard.report.sightings
        assert postcard.medias

        media = next(iter(postcard.medias), None)

        species: Species | None = None
        if unlocked := [
            s for s in postcard.report.sightings if s.sighting_type.is_unlocked
        ]:
            species = unlocked[0].species
            LOGGER.debug(
                "Reporting recent visitor from unlocked: %s", species.name
            )
        elif recognized := [
            s for s in postcard.report.sightings if s.sighting_type.is_recognized
        ]:
            species = recognized[0].species
            LOGGER.debug(
                "Reporting recent visitor from recognized: %s", species.name
            )
        elif guessable := [s for s in postcard.report.sightings if s.suggestions]:
            species = guessable[0].suggestions[0].species
            LOGGER.info(
                "Reporting recent visitor from unrecognized suggestion: %s",
                species.name,
            )
        else:
            LOGGER.info("Cannot decide species: %s", postcard.report.sightings[0])

        if media:
            # Prepend a fresh entry; trim to count. The next poll's
            # `_update_latest_visitor` will rebuild from the feed (with fresh
            # signed URLs), so we don't need to refresh URLs ourselves.
            entry = RecentVisitor(
                media=media,
                species=species,
                created_at=media.created_at,
            )
            self._recent = ([entry] + self._recent)[: self._count]
            self._fallback_species = None
        elif species:
            self._fallback_species = species

        LOGGER.debug(
            "Setting recent visitor on %s from postcard: %s, %s: %s",
            self.feeder.name,
            species.name if species else "Unknown species",
            media.created_at if media else "no media",
            media.content_url if media else "no url",
        )

        self._notify_listeners()
