"""Helpers for managing recent visitors."""

from dataclasses import dataclass
from typing import TypeVar
from collections.abc import Callable

from birdbuddy.birds import Species
from birdbuddy.client import BirdBuddy
from birdbuddy.feed import FeedNode, FeedNodeType
from birdbuddy.feeder import Feeder
from birdbuddy.media import Media, is_media_expired
from birdbuddy.sightings import PostcardSighting

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CALLBACK_TYPE

from .const import EVENT_NEW_POSTCARD_SIGHTING, LOGGER, RECENT_VISITOR_COUNT
from .util import _find_media

# pybirdbuddy's FEED query does not request `medias` on FeedItemNewPostcard, but
# the field exists server-side and carries the postcard image *before* any
# identification. For feeders without auto-ID, whose postcards never convert to
# sightings (BB returns INTERNAL_SERVER_ERROR), this is the ONLY source of a
# visitor image. We fetch it with our own query. See issue #7.
# `medias` is an interface, so each concrete type is selected via an inline
# fragment, and `contentUrl` requires the MediaImageSize argument.
_FEED_WITH_MEDIAS_QUERY = """
query GetFeedWithMedias {
  me {
    feed(first: 50) {
      edges {
        node {
          __typename
          ... on FeedItemNewPostcard {
            id
            createdAt
            medias {
              __typename
              id
              createdAt
              thumbnailUrl
              ... on MediaImage {
                contentUrl(size: ORIGINAL)
              }
            }
          }
        }
      }
    }
  }
}
"""

# Feed node types that can carry a visitor image we can surface. Notably absent:
# `NewPostcard` — it has no media in the feed, and the postcard→sighting call
# that would yield media returns INTERNAL_SERVER_ERROR for feeders without
# auto-ID (see coordinator._process_feed). See issue #7.
_VISITOR_FEED_TYPES = (
    FeedNodeType.SpeciesSighting,
    FeedNodeType.SpeciesUnlocked,
    FeedNodeType.CollectedPostcard,
    FeedNodeType.MysteryVisitorNotRecognized,
    FeedNodeType.MysteryVisitorResolved,
)

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
        # Set by the coordinator each poll: when the account has a single
        # feeder, postcard media can be attributed to it without relying on the
        # feeder-id-in-URL heuristic. See `_fetch_postcard_media`.
        self._sole_feeder: bool = False

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

    async def async_update(self, *, sole_feeder: bool = False) -> None:
        """Refresh the recent-visitor list from the feed (poll-driven).

        Called by the coordinator on every poll. This is the *only* refresh
        path that works for feeders without auto-ID: their visits arrive as
        postcards that can't be converted to sightings, so the event-driven
        path (`_on_new_postcard`) never fires for them. See issue #7.

        `sole_feeder` is True when this is the only feeder on the account, which
        lets postcard media be attributed without the feeder-id-in-URL heuristic.
        """
        self._sole_feeder = sole_feeder
        await self._update_latest_visitor()

    async def _update_latest_visitor(self) -> None:
        feed = await self.client.feed()

        # Pull the whole feed once, then split by type in Python. This lets us
        # log exactly what the feed contains (below) — crucial because the
        # recent-visitor list can only be built from node types that carry
        # media. If a feeder's visits arrive as some other type (e.g. raw
        # `NewPostcard`, which has NO media in the feed at all), nothing
        # downstream can surface them and this log is the only place that shows
        # it. See issue #7.
        all_nodes = feed.filter()
        type_counts: dict[str, int] = {}
        for node in all_nodes:
            key = node.node_type.value
            type_counts[key] = type_counts.get(key, 0) + 1

        items = [n for n in all_nodes if n.node_type in _VISITOR_FEED_TYPES]
        my_items = _find_media(self.feeder.id, items)

        # Raw (unidentified) postcards carry no media in pybirdbuddy's feed, so
        # fetch their images separately. This is what makes feeders without
        # auto-ID work at all. See issue #7.
        postcard_items = await self._fetch_postcard_media()

        # Merge, keying by feed-node id. Insert postcard items first so a typed
        # entry (which carries species) wins if the same node appears in both.
        by_id: dict[str, FeedNode] = {}
        for item in postcard_items:
            by_id[item.get("id")] = item
        for item in my_items:
            by_id[item.get("id")] = item
        combined = list(by_id.values())

        LOGGER.debug(
            "Feeder %s: feed has %d node(s) %s; %d media-bearing item(s) of "
            "interest, %d matching this feeder, %d raw-postcard image(s)",
            self.feeder.name,
            len(all_nodes),
            type_counts,
            len(items),
            len(my_items),
            len(postcard_items),
        )

        # Rebuild the recent list from the feed each poll. Caching-and-appending
        # would let stale signed CloudFront URLs linger in the list past their
        # TTL; rebuilding gives every entry a fresh URL for free and is
        # restart-safe.
        sorted_items = sorted(combined, key=lambda x: x.created_at, reverse=True)
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

    async def _fetch_postcard_media(self) -> list[FeedNode]:
        """Fetch image media for raw (unidentified) postcards.

        pybirdbuddy's feed query omits `medias` on FeedItemNewPostcard, so we
        ask for the field directly. These postcards carry no species (the bird
        hasn't been identified), so the resulting entries surface with
        `species=None`. See issue #7.
        """
        try:
            result = await self.client._make_request(query=_FEED_WITH_MEDIAS_QUERY)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug(
                "Postcard-media query failed for %s: %s", self.feeder.name, exc
            )
            return []

        edges = (((result or {}).get("me") or {}).get("feed") or {}).get("edges") or []
        nodes = [
            FeedNode(edge["node"])
            for edge in edges
            if edge.get("node", {}).get("__typename") == "FeedItemNewPostcard"
        ]
        if not nodes:
            return []

        matched = _find_media(self.feeder.id, nodes)
        if not matched and self._sole_feeder:
            # The feeder id isn't in these URLs, but with a single feeder on the
            # account every postcard image is necessarily ours.
            matched = _find_media(
                self.feeder.id, nodes, require_feeder_match=False
            )
            LOGGER.debug(
                "Feeder %s: attributed %d raw-postcard image(s) without a URL "
                "match (sole feeder on account)",
                self.feeder.name,
                len(matched),
            )
        return matched

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
