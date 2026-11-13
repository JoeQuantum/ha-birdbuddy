"""Coherent feeder views of canonical account-scoped visits."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from time import monotonic
from weakref import WeakKeyDictionary, WeakSet

from birdbuddy.birds import Species
from birdbuddy.client import BirdBuddy
from birdbuddy.exceptions import GraphqlError
from birdbuddy.feeder import Feeder
from birdbuddy.media import Media
from birdbuddy.visits import Visit, normalize_visit

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .const import RECENT_VISITOR_COUNT
from .util import media_preview_url

VisitorCallback = Callable[["RecentVisitors"], None]
_VISITOR_KINDS = {
    "FeedItemNewPostcard",
    "FeedItemCollectedPostcard",
    "FeedItemSpeciesSighting",
    "FeedItemSpeciesUnlocked",
    "FeedItemMysteryVisitorNotRecognized",
    "FeedItemMysteryVisitorResolved",
}
_REFRESH_INTERVAL = 30


@dataclass(frozen=True)
class RecentVisitor:
    """One visit, including unknown species or unavailable preview media."""

    media: Media | None
    species: Species | None
    created_at: datetime | None
    visit_id: str | None = None


@dataclass
class _AccountRefresh:
    """Coalesce read refreshes by account client and visit identity."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    consumers: WeakSet[RecentVisitors] = field(default_factory=WeakSet)
    next_attempt: dict[str | None, float] = field(default_factory=dict)


_ACCOUNT_REFRESH: WeakKeyDictionary[BirdBuddy, _AccountRefresh] = WeakKeyDictionary()


class RecentVisitors:
    """A feeder's ordered history; no independent feed polling or bus mutations."""

    def __init__(
        self,
        feeder: Feeder,
        client: BirdBuddy,
        hass: HomeAssistant,
        *,
        count: int = RECENT_VISITOR_COUNT,
        dedupe_by_species: bool = False,
    ) -> None:
        self.hass = hass
        self.client = client
        self.feeder = feeder
        self.dedupe_by_species = dedupe_by_species
        self._count = count
        self._listeners: set[VisitorCallback] = set()
        self._recent: list[RecentVisitor] = []
        self._visits: dict[str, Visit] = {}
        self._generation = 0
        state = _ACCOUNT_REFRESH.setdefault(client, _AccountRefresh())
        state.consumers.add(self)

    @property
    def recent(self) -> list[RecentVisitor]:
        """Return a copy of the newest-first carousel."""
        return list(self._recent)

    @property
    def latest_media(self) -> Media | None:
        """The latest visit's media, never an older visit's image."""
        return self._recent[0].media if self._recent else None

    @property
    def latest_species(self) -> Species | None:
        """The latest visit's recognized species; suggestions remain unknown."""
        return self._recent[0].species if self._recent else None

    @callback
    def register_callback(self, listener: VisitorCallback) -> CALLBACK_TYPE:
        """Seed a consumer immediately, without scheduling another feed read."""
        self._listeners.add(listener)
        listener(self)
        return lambda: self.unregister_callback(listener)

    @callback
    def unregister_callback(self, listener: VisitorCallback) -> None:
        """Remove a consumer idempotently during entity unload."""
        self._listeners.discard(listener)

    @callback
    def apply_visits(self, visits: Sequence[Visit], *, replace: bool = False) -> None:
        """Apply one poll/event batch, then notify consumers from the same state.

        Missing fields on one visit cannot erase other identities. A complete
        authoritative reset must explicitly use ``replace=True``. Equal capture
        timestamps use descending feed-item ID as a stable tie breaker. The
        default preserves consecutive visits of the same species.
        """
        updated = {} if replace else dict(self._visits)
        for visit in visits:
            if not visit.item_id or visit.kind not in _VISITOR_KINDS:
                continue
            if visit.feeder_id == self.feeder.id:
                updated[visit.item_id] = visit
            elif visit.feeder_id is not None:
                updated.pop(visit.item_id, None)
        ordered = sorted(
            updated.values(),
            key=lambda visit: (
                visit.created_at or datetime.min.replace(tzinfo=UTC),
                visit.item_id or "",
            ),
            reverse=True,
        )
        retained = max(20, self._count) if self.dedupe_by_species else self._count
        recent = self._build_recent(ordered)
        displayed_ids = {entry.visit_id for entry in recent}
        retained_visits = {
            visit.item_id: visit
            for position, visit in enumerate(ordered)
            if visit.item_id and (position < retained or visit.item_id in displayed_ids)
        }
        if not replace and retained_visits == self._visits:
            return
        self._visits = retained_visits
        self._recent = recent
        self._generation += 1
        self._notify_listeners()

    def _build_recent(self, visits: Sequence[Visit]) -> list[RecentVisitor]:
        result = []
        seen_species: set[str] = set()
        for visit in visits:
            species = next(iter(visit.species), None)
            species_key = (species.id or species.name) if species else None
            if self.dedupe_by_species and species_key:
                if species_key in seen_species:
                    continue
                seen_species.add(species_key)
            # A video's thumbnail is a preview; its content URL stays a video.
            asset = (
                next(
                    (
                        media
                        for media in sorted(
                            visit.medias, key=lambda item: not item.is_image
                        )
                        if (media.is_image or media.is_video)
                        and media_preview_url(Media(media.to_dict()))
                    ),
                    None,
                )
                or visit.representative_image
                or next((media for media in visit.medias if media.is_video), None)
            )
            result.append(
                RecentVisitor(
                    media=Media(asset.to_dict()) if asset else None,
                    species=(
                        Species(
                            {
                                key: value
                                for key, value in species.to_dict().items()
                                if value is not None
                            }
                        )
                        if species and species.name
                        else None
                    ),
                    created_at=visit.created_at,
                    visit_id=visit.item_id,
                )
            )
            if len(result) >= self._count:
                break
        return result

    async def async_update(
        self, *, visit_id: str | None = None, sole_feeder: bool = False
    ) -> None:
        """Refresh one displayed visit read-only, coalesced within its account.

        ``sole_feeder`` remains accepted for older image consumers but never
        authorizes attribution. With no retained identity, one account feed
        page seeds all active feeder views. Normal polling calls apply_visits.
        """
        del sole_feeder
        if visit_id is None and self._recent:
            visit_id = self._recent[0].visit_id
        state = _ACCOUNT_REFRESH.setdefault(self.client, _AccountRefresh())
        async with state.lock:
            if monotonic() < state.next_attempt.get(visit_id, 0):
                return
            state.next_attempt[visit_id] = monotonic() + _REFRESH_INTERVAL
            # Keep the refresh-budget map bounded as visits roll out of view.
            active_ids = {
                entry.visit_id
                for consumer in state.consumers
                for entry in consumer._recent
            }
            state.next_attempt = {
                key: value
                for key, value in state.next_attempt.items()
                if key in active_ids or key == visit_id or key is None
            }
            consumers = {consumer: consumer._generation for consumer in state.consumers}
            try:
                if visit_id:
                    visits = [await self.client.get_feed_item(visit_id)]
                else:
                    feed = await self.client.feed(first=50)
                    known = tuple(self.client.feeders)
                    visits = [
                        normalize_visit(node, known_feeder_ids=known)
                        for node in feed.validated_nodes()
                    ]
            except GraphqlError as exc:
                if visit_id and exc.error_code in {"NOT_FOUND", "FEED_ITEM_NOT_FOUND"}:
                    self._clear_missing_assets(visit_id, consumers)
                    return
                raise
            for consumer, generation in consumers.items():
                # A newer poll or explicit clear wins a race with this read.
                if consumer._generation == generation:
                    consumer.apply_visits(visits)

    @staticmethod
    def _clear_missing_assets(
        visit_id: str, consumers: dict[RecentVisitors, int]
    ) -> None:
        for consumer, generation in consumers.items():
            if consumer._generation == generation and (
                visit := consumer._visits.get(visit_id)
            ):
                consumer.apply_visits([replace(visit, medias=())])

    def _notify_listeners(self) -> None:
        for listener in tuple(self._listeners):
            listener(self)
