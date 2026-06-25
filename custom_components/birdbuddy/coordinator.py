"""Data Update coordinator for Bird Buddy."""

from __future__ import annotations

from collections import deque
from datetime import timedelta

from birdbuddy.client import BirdBuddy
from birdbuddy.exceptions import GraphqlError
from birdbuddy.feed import FeedNode, FeedNodeType
from birdbuddy.feeder import Feeder
from birdbuddy.media import Collection
from birdbuddy.sightings import PostcardSighting, SightingFinishStrategy
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import EventOrigin, HomeAssistant
from homeassistant.helpers.update_coordinator import (
    CALLBACK_TYPE,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_POLLING_INTERVAL,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
    EVENT_NEW_FEED_ITEM,
    EVENT_NEW_POSTCARD_SIGHTING,
    LOGGER,
)
from .device import BirdBuddyDevice
from .util import _media_list, slim_event_payload
from .visitors import _FEED_WITH_MEDIAS_QUERY, RecentVisitors, VisitorCallback

# Visitor-bearing feed node types that warrant an EVENT_NEW_FEED_ITEM. Unlike the
# recent-visitor surface (which only keeps types that already carry media),
# NewPostcard is included here: its image is fetched separately via the
# postcard-media query so even unidentified visits emit an event with a picture.
_NEW_FEED_ITEM_TYPES = (
    FeedNodeType.NewPostcard,
    FeedNodeType.CollectedPostcard,
    FeedNodeType.SpeciesSighting,
    FeedNodeType.SpeciesUnlocked,
    FeedNodeType.MysteryVisitorNotRecognized,
    FeedNodeType.MysteryVisitorResolved,
)

# Cap on the in-memory set of already-emitted feed-item ids. Bounded so it can't
# grow unboundedly; NOT persisted to the config entry (birdsense persists and
# grows .storage without limit).
_SEEN_FEED_ITEM_CAP = 500


class BirdBuddyDataUpdateCoordinator(DataUpdateCoordinator[BirdBuddy]):
    """Class to coordinate fetching BirdBuddy data."""

    config_entry: ConfigEntry
    client: BirdBuddy
    feeders: dict[str, BirdBuddyDevice]
    visitors: dict[str, RecentVisitors]

    def __init__(
        self,
        hass: HomeAssistant,
        client: BirdBuddy,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the BirdBuddy data coordinator."""
        self.client = client
        self.feeders = {}
        self.visitors = {}
        self.first_update = True
        # UTC timestamp of the last successful poll; surfaced by the Last Sync
        # sensor. None until the first success.
        self.last_update_timestamp = None
        # In-memory dedup of feed-item ids already emitted as EVENT_NEW_FEED_ITEM.
        # The set gives O(1) membership; the deque tracks recency so we can cap
        # the set to the most recent _SEEN_FEED_ITEM_CAP ids. Deliberately not
        # persisted — it only needs to suppress repeats within a running session,
        # and on restart the first poll re-seeds without firing.
        self._seen_feed_ids: set[str] = set()
        self._seen_feed_order: deque[str] = deque(maxlen=_SEEN_FEED_ITEM_CAP)
        # Whether the one-time seed pass has run. Until it has, the next poll
        # marks the existing backlog as seen WITHOUT firing, so a restart never
        # replays old items as new events.
        self._feed_events_seeded: bool = False
        # Poll cadence is user-configurable via the options flow; default keeps
        # the historical 10-minute interval. A changed option reloads the entry
        # (see __init__.py update listener), rebuilding the coordinator.
        minutes = entry.options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )

    def add_visitor_listener(
        self, feeder: Feeder, listener: VisitorCallback
    ) -> CALLBACK_TYPE:
        """Register a callback to be called when a new visitor is detected."""
        if feeder.id not in self.visitors:
            self.visitors[feeder.id] = RecentVisitors(feeder, self.client, self.hass)
        return self.visitors[feeder.id].register_callback(listener)

    async def _process_feed(self, feed: list[FeedNode]) -> bool:
        """Attempt to process new feed items.

        There are some options for how we can process these:
        - If the sighting contains a recognized bird, we can finish it automatically
          using :func:`BirdBuddy.finish_postcard`.
        - For all new postcards, we can simply emit a HA event, and leave it up to
          the user's automations to finish them, however (and if) the user wants.
        """
        LOGGER.debug("Found feed items %s", feed)
        postcards = [
            node for node in feed if node.node_type == FeedNodeType.NewPostcard
        ]

        for node in feed:
            if node.node_type == FeedNodeType.SpeciesUnlocked and (
                c := Collection(node.get("collection"))
            ):
                LOGGER.info("Recently unlocked species: %s", c.bird_name)
                self.client.collections.setdefault(c.collection_id, c)

        LOGGER.debug("Found postcards %s", postcards)
        for postcard in postcards:
            LOGGER.debug("A new postcard is ready to process: %s", postcard)
            if not self.hass.bus.async_listeners().get(EVENT_NEW_POSTCARD_SIGHTING):
                # if no one is listening, no sense in getting sighting data
                LOGGER.debug("No event listeners: skipping postcard conversion")
                continue

            # emit a new event with sighting data and postcard data
            # expose services that can:
            # 1. auto-collect a recognized bird
            # 2. manually assign a species
            # 3. auto-collect a best-guess species, using sightingReport confidence
            # 4. assign the sighting as "mystery visitor"
            # 5. all-in-one service that can choose the best option of 1, 3, or 4
            # Automations could use the sighting media URLs to do additional AI processing,
            # such as with Merlin or other AI classifiers, and then do #2 with the results.
            # If this is a viable option, we can supply a Recipe in docs to show how this could
            # be done. Similarly, we can supply some default blueprints to handle this with
            # user input.
            try:
                sighting = await self.client.sighting_from_postcard(postcard=postcard)
            except GraphqlError as err:
                # Upstream #98: sightingCreateFromPostcard intermittently returns
                # INTERNAL_SERVER_ERROR. This affects ONLY the auto-collect path
                # (turning a postcard into a saved sighting for the
                # collect_postcard service / Media Browser). It does NOT affect
                # the recent-visitor image, which is fetched directly from the
                # postcard feed node — see visitors._fetch_postcard_media. Skip
                # just this postcard so one failure doesn't fail the whole update
                # cycle (which would lose all subsequent postcards too, since
                # refresh_feed has already advanced its cursor past them).
                LOGGER.warning(
                    "Postcard %s could not be auto-collected into a sighting "
                    "(%s). This affects only the collect_postcard path; the "
                    "recent-visitor image is fetched separately and is "
                    "unaffected. Skipping this postcard.",
                    postcard.node_id,
                    err,
                )
                continue
            data = slim_event_payload(postcard.data, sighting.data)
            self.hass.bus.fire(
                event_type=EVENT_NEW_POSTCARD_SIGHTING,
                event_data=data,
                origin=EventOrigin.remote,
            )

    async def _async_update_data(self) -> BirdBuddy:
        try:
            await self.client.refresh()

            # Skip processing the Feed on the first update. This works around a minor issue
            # where the `automation` integration is not loaded yet by the time we make our first
            # update call. If we proceed, we might emit the postcard feed items while there are
            # no automations listening; and because refresh_feed() keeps track of the last seen
            # feed item timestamp, that would prevent seeing that postcard again.
            # This delays the first attempt at postcard handling until the next update interval.
            if not self.first_update:
                feed = await self.client.refresh_feed()
                await self._process_feed(feed)
        except Exception as exc:
            raise UpdateFailed(exc) from exc

        if not self.client.feeders:
            raise UpdateFailed("No Feeders found")

        feeders = {
            id: BirdBuddyDevice(f) for (id, f) in self.client.feeders.items()
        }  # noqa: A001
        # pylint: disable=invalid-name
        for i, f in feeders.items():
            if i in self.feeders:
                self.feeders[i].update(f)
            else:
                self.feeders[i] = f
        is_first_update = self.first_update
        self.first_update = False

        # Emit an event per newly-seen visitor-bearing feed item. Skip the very
        # first (setup) poll entirely, mirroring the _process_feed first-update
        # skip: no feed is fetched during setup, and fetching one here purely to
        # seed would be a network call setup doesn't need — and would leak a DNS
        # resolver thread under aiodns. The first poll AFTER setup seeds the
        # existing backlog without firing (so a restart never replays it); only
        # later polls fire. A failure here must not fail the whole update.
        if not is_first_update:
            try:
                await self._fire_new_feed_item_events(
                    seed_only=not self._feed_events_seeded
                )
                self._feed_events_seeded = True
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug(
                    "Firing %s events failed: %s", EVENT_NEW_FEED_ITEM, exc
                )

        # Refresh the recent-visitor surface every poll. The event-driven path
        # (RecentVisitors._on_new_postcard) only fires when a postcard converts
        # to a sighting, which never happens for feeders without auto-ID — Bird
        # Buddy returns INTERNAL_SERVER_ERROR (see _process_feed). Polling the
        # feed here is the only way those feeders' mystery visitors ever surface
        # after startup (#7). A refresh failure must not fail the whole update.
        sole_feeder = len(self.client.feeders) == 1
        for feeder_id, visitors in self.visitors.items():
            try:
                await visitors.async_update(sole_feeder=sole_feeder)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug(
                    "Recent-visitor refresh failed for feeder %s: %s",
                    feeder_id,
                    exc,
                )

        # Mark a successful poll (reached only if no UpdateFailed was raised).
        self.last_update_timestamp = dt_util.utcnow()

        return self.client

    def _mark_feed_item_seen(self, item_id: str) -> None:
        """Record a feed-item id, evicting the oldest once the cap is reached."""
        if item_id in self._seen_feed_ids:
            return
        if len(self._seen_feed_order) == self._seen_feed_order.maxlen:
            # The deque is full; appending will drop the leftmost id, so remove
            # it from the membership set first to keep the two in sync.
            self._seen_feed_ids.discard(self._seen_feed_order[0])
        self._seen_feed_order.append(item_id)
        self._seen_feed_ids.add(item_id)

    def _feeder_for_url(self, url: str | None) -> str | None:
        """Best-effort feeder id for a media URL (the feed has no per-item feeder
        field; the feeder id is embedded in the signed media URL)."""
        if not url:
            return None
        return next((fid for fid in self.client.feeders if fid in url), None)

    async def _fetch_new_postcard_media_map(self) -> dict[str, dict]:
        """Map NewPostcard id -> first MediaImage dict, via the postcard-media
        query (pybirdbuddy's feed omits `medias` on NewPostcard). Reuses the
        v0.1.8 query so unidentified postcards still carry an image."""
        try:
            result = await self.client._make_request(query=_FEED_WITH_MEDIAS_QUERY)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Postcard-media query failed for feed events: %s", exc)
            return {}
        edges = (((result or {}).get("me") or {}).get("feed") or {}).get("edges") or []
        out: dict[str, dict] = {}
        for edge in edges:
            node = edge.get("node") or {}
            if node.get("__typename") != "FeedItemNewPostcard":
                continue
            images = [
                m for m in (node.get("medias") or [])
                if m.get("__typename") == "MediaImage"
            ]
            if images and node.get("id"):
                out[node["id"]] = images[0]
        return out

    async def _fire_new_feed_item_events(self, *, seed_only: bool) -> None:
        """Fire EVENT_NEW_FEED_ITEM once per newly-seen visitor-bearing item."""
        feed = await self.client.feed()
        items = [
            node
            for node in feed.filter(of_type=list(_NEW_FEED_ITEM_TYPES))
            if node.node_id and node.node_id not in self._seen_feed_ids
        ]
        if seed_only:
            for node in items:
                self._mark_feed_item_seen(node.node_id)
            return
        if not items:
            return

        postcard_media: dict[str, dict] = {}
        if any(n.node_type == FeedNodeType.NewPostcard for n in items):
            postcard_media = await self._fetch_new_postcard_media_map()

        for node in items:
            self._fire_one_feed_item(node, postcard_media)
            self._mark_feed_item_seen(node.node_id)

    def _fire_one_feed_item(
        self, node: FeedNode, postcard_media: dict[str, dict]
    ) -> None:
        """Build and fire a slim EVENT_NEW_FEED_ITEM payload for one node.

        Slim by construction (refs + the fields automations need, not the full
        nested feed blob), so it stays well under HA's 32768-byte recorder cap
        (#78)."""
        item_id = node.node_id
        if node.node_type == FeedNodeType.NewPostcard:
            media = postcard_media.get(item_id)
        else:
            images = [
                m for m in _media_list(node)
                if m and m.get("__typename") == "MediaImage"
            ]
            media = images[0] if images else None

        content_url = media.get("contentUrl") if media else None
        thumbnail_url = media.get("thumbnailUrl") if media else None
        feeder_id = self._feeder_for_url(content_url) or self._feeder_for_url(
            thumbnail_url
        )

        payload = {
            "item_id": item_id,
            "type": node.node_type.value,
            "created_at": node.get("createdAt"),
            "feeder_id": feeder_id,
            "media_url": content_url,
            "thumbnail_url": thumbnail_url,
        }
        self.hass.bus.fire(
            event_type=EVENT_NEW_FEED_ITEM,
            event_data=payload,
            origin=EventOrigin.remote,
        )

    async def handle_collect_postcard(self, data: dict[str, any]) -> bool:
        """Handle the `birdbuddy.collect_postcard` service call."""
        sighting = PostcardSighting(data["sighting"])
        postcard_id = data["postcard"]["id"]
        strategy = SightingFinishStrategy(data.get("strategy", "recognized"))
        confidence = data.get("best_guess_confidence")
        share_media = data.get("share_media", False)

        LOGGER.debug(
            "Calling collect_postcard: id=%s, sighting=%s, strategy=%s",
            postcard_id,
            sighting,
            strategy,
        )
        success = await self.client.finish_postcard(
            postcard_id,
            sighting,
            strategy,
            confidence_threshold=confidence,
            share_media=share_media,
        )
        if success:
            LOGGER.info("Postcard collected to Media")
        else:
            # TODO: more info
            LOGGER.warning("Postcard could not be collected")
        return success
