"""Tests for custom_components.birdbuddy.visitors."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from birdbuddy.feed import FeedNode

from custom_components.birdbuddy.const import RECENT_VISITOR_COUNT
from custom_components.birdbuddy.visitors import RecentVisitor, RecentVisitors


def _feed_item(
    *,
    feeder_id: str = "f1",
    species_id: str,
    species_name: str,
    media_id: str,
    media_url: str,
    created_at: datetime,
) -> FeedNode:
    """Mimic a real feed entry as the integration sees it.

    Wraps the raw dict in `FeedNode` because `_find_media_with_species`
    preserves the type through `item | {"media": ...}` and the downstream
    code reads `item.created_at` (a FeedNode property) for sorting.
    """
    return FeedNode(
        {
            "__typename": "FeedItemSpeciesSighting",
            "createdAt": created_at.isoformat(),
            "medias": [
                {
                    "__typename": "MediaImage",
                    "id": media_id,
                    "contentUrl": media_url,
                    "thumbnailUrl": (
                        f"https://thumb.example.com/{feeder_id}/{media_id}"
                    ),
                    "createdAt": created_at.isoformat(),
                }
            ],
            "species": [
                {
                    "__typename": "SpeciesBird",
                    "id": species_id,
                    "name": species_name,
                    "iconUrl": f"https://icon.example.com/{species_id}",
                }
            ],
        }
    )


def _make_visitors(
    *,
    feeder_id: str = "f1",
    feeder_name: str = "Backyard",
    count: int = RECENT_VISITOR_COUNT,
    dedupe_by_species: bool = False,
) -> RecentVisitors:
    """Build a RecentVisitors without engaging HA init.

    The class doesn't subclass anything special, so a normal __init__ works
    if we provide a mocked client + hass + feeder. The collections fallback
    path in `_update_latest_visitor` calls `client.refresh_collections()`;
    default it to an empty dict so the fallback no-ops unless a test wires it.
    """
    client = MagicMock()
    client.refresh_collections = AsyncMock(return_value={})
    return RecentVisitors(
        feeder=SimpleNamespace(id=feeder_id, name=feeder_name),
        client=client,
        hass=MagicMock(),
        count=count,
        dedupe_by_species=dedupe_by_species,
    )


def test_update_rebuilds_recent_top_n_sorted_by_created_at() -> None:
    """`_update_latest_visitor` must build a list of up to N entries, sorted
    newest-first. Rebuilding from the feed each poll (rather than appending
    to a cache) means signed CloudFront URLs in `recent` are always fresh
    and restart-safe."""
    visitors = _make_visitors(count=3)

    # Build a feed with 5 items in shuffled timestamp order.
    base = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    items = [
        _feed_item(
            species_id="s1",
            species_name="Cardinal",
            media_id="m1",
            media_url="https://cdn.example.com/m1.jpg",
            created_at=base.replace(hour=10),  # 4th
        ),
        _feed_item(
            species_id="s2",
            species_name="Goldfinch",
            media_id="m2",
            media_url="https://cdn.example.com/m2.jpg",
            created_at=base.replace(hour=12),  # 2nd
        ),
        _feed_item(
            species_id="s3",
            species_name="Bluejay",
            media_id="m3",
            media_url="https://cdn.example.com/m3.jpg",
            created_at=base.replace(hour=13),  # 1st (newest)
        ),
        _feed_item(
            species_id="s4",
            species_name="Robin",
            media_id="m4",
            media_url="https://cdn.example.com/m4.jpg",
            created_at=base.replace(hour=11),  # 3rd
        ),
        _feed_item(
            species_id="s5",
            species_name="Sparrow",
            media_id="m5",
            media_url="https://cdn.example.com/m5.jpg",
            created_at=base.replace(hour=9),  # 5th (oldest, must be trimmed)
        ),
    ]

    feed = MagicMock()
    feed.filter.return_value = items
    visitors.client.feed = AsyncMock(return_value=feed)

    asyncio.run(visitors._update_latest_visitor())

    # Top 3 sorted newest-first.
    assert len(visitors.recent) == 3
    species_order = [v.species.name for v in visitors.recent]
    assert species_order == ["Bluejay", "Goldfinch", "Robin"]


def test_latest_media_and_species_return_head_for_backward_compat() -> None:
    """`latest_media` / `latest_species` must remain truthful so the image
    entity and recent_visitor sensor (which still read them) keep working
    unchanged after the carousel refactor."""
    visitors = _make_visitors(count=5)

    base = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    items = [
        _feed_item(
            species_id="s_newest",
            species_name="Cardinal",
            media_id="m_newest",
            media_url="https://cdn.example.com/newest.jpg",
            created_at=base.replace(hour=13),
        ),
        _feed_item(
            species_id="s_older",
            species_name="Goldfinch",
            media_id="m_older",
            media_url="https://cdn.example.com/older.jpg",
            created_at=base.replace(hour=11),
        ),
    ]
    feed = MagicMock()
    feed.filter.return_value = items
    visitors.client.feed = AsyncMock(return_value=feed)

    asyncio.run(visitors._update_latest_visitor())

    assert visitors.latest_media is visitors.recent[0].media
    assert visitors.latest_media.content_url == "https://cdn.example.com/newest.jpg"
    assert visitors.latest_species.name == "Cardinal"


def test_dedupe_off_preserves_consecutive_same_species() -> None:
    """Default (dedupe_by_species=False): the same species back-to-back must
    keep its multiple entries. The motivating busy-feeder request explicitly
    wants distinct visit timestamps, not species buckets."""
    visitors = _make_visitors(count=5, dedupe_by_species=False)

    base = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    items = [
        _feed_item(
            species_id="s1",
            species_name="Cardinal",
            media_id=f"m{i}",
            media_url=f"https://cdn.example.com/m{i}.jpg",
            created_at=base.replace(hour=13 - i),
        )
        for i in range(4)
    ]
    feed = MagicMock()
    feed.filter.return_value = items
    visitors.client.feed = AsyncMock(return_value=feed)

    asyncio.run(visitors._update_latest_visitor())

    assert len(visitors.recent) == 4
    assert all(v.species.name == "Cardinal" for v in visitors.recent)


def test_dedupe_on_collapses_same_species() -> None:
    """When wired (no UI today, but the toggle exists), dedupe must keep
    only the newest occurrence of each species."""
    visitors = _make_visitors(count=5, dedupe_by_species=True)

    base = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    # Newest-first the feed has: Cardinal, Cardinal, Goldfinch, Cardinal, Bluejay
    items = [
        _feed_item(
            species_id="s_card",
            species_name="Cardinal",
            media_id=f"m_card_{i}",
            media_url=f"https://cdn.example.com/card{i}.jpg",
            created_at=base.replace(hour=13 - i),
        )
        for i in range(2)
    ] + [
        _feed_item(
            species_id="s_gold",
            species_name="Goldfinch",
            media_id="m_gold",
            media_url="https://cdn.example.com/gold.jpg",
            created_at=base.replace(hour=10),
        ),
        _feed_item(
            species_id="s_card",
            species_name="Cardinal",
            media_id="m_card_old",
            media_url="https://cdn.example.com/cardold.jpg",
            created_at=base.replace(hour=9),
        ),
        _feed_item(
            species_id="s_blue",
            species_name="Bluejay",
            media_id="m_blue",
            media_url="https://cdn.example.com/blue.jpg",
            created_at=base.replace(hour=8),
        ),
    ]
    feed = MagicMock()
    feed.filter.return_value = items
    visitors.client.feed = AsyncMock(return_value=feed)

    asyncio.run(visitors._update_latest_visitor())

    species_names = [v.species.name for v in visitors.recent]
    assert species_names == ["Cardinal", "Goldfinch", "Bluejay"]


def test_empty_feed_is_safe_and_falls_back_to_collections_species_only() -> None:
    """No matching feed items: `recent` must be empty (not stale) and
    `latest_species` must surface the collections-fallback species so the
    sensor still reports something."""
    visitors = _make_visitors()

    feed = MagicMock()
    feed.filter.return_value = []
    visitors.client.feed = AsyncMock(return_value=feed)
    fallback_collection = SimpleNamespace(
        feeder_name="Backyard",
        last_visit=datetime(2026, 5, 30, tzinfo=timezone.utc),
        species=SimpleNamespace(name="Sparrow"),
    )
    visitors.client.refresh_collections = AsyncMock(
        return_value={"c1": fallback_collection}
    )

    asyncio.run(visitors._update_latest_visitor())

    assert visitors.recent == []
    assert visitors.latest_media is None
    assert visitors.latest_species.name == "Sparrow"


def test_empty_feed_with_no_collections_yields_empty_state() -> None:
    """Defensive: feed empty AND no matching collections → both are None,
    no exceptions."""
    visitors = _make_visitors()

    feed = MagicMock()
    feed.filter.return_value = []
    visitors.client.feed = AsyncMock(return_value=feed)

    asyncio.run(visitors._update_latest_visitor())

    assert visitors.recent == []
    assert visitors.latest_media is None
    assert visitors.latest_species is None


def test_rebuild_replaces_stale_entries_each_poll() -> None:
    """If the feed loses an entry (it aged out / was cleaned up server-side),
    the next poll must drop it from `recent` rather than carrying it forward
    with its now-expired URL."""
    visitors = _make_visitors(count=3)

    base = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    first_poll = [
        _feed_item(
            species_id=f"s{i}",
            species_name=f"Bird{i}",
            media_id=f"m{i}",
            media_url=f"https://cdn.example.com/m{i}.jpg",
            created_at=base.replace(hour=13 - i),
        )
        for i in range(3)
    ]
    second_poll = [first_poll[0]]  # only the newest survives

    feed = MagicMock()
    feed.filter.side_effect = [first_poll, second_poll]
    visitors.client.feed = AsyncMock(return_value=feed)

    asyncio.run(visitors._update_latest_visitor())
    assert len(visitors.recent) == 3

    asyncio.run(visitors._update_latest_visitor())
    assert len(visitors.recent) == 1
    assert visitors.recent[0].species.name == "Bird0"


def test_recent_entries_are_serializable_to_sensor_attribute_shape() -> None:
    """`RecentVisitor` carries the fields the sensor needs (`media`, `species`,
    `created_at`) so the `visitors` attribute mapping can be built directly
    from it without translation glue."""
    visitors = _make_visitors(count=2)

    base = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    items = [
        _feed_item(
            species_id="s1",
            species_name="Cardinal",
            media_id="m1",
            media_url="https://cdn.example.com/m1.jpg",
            created_at=base,
        ),
    ]
    feed = MagicMock()
    feed.filter.return_value = items
    visitors.client.feed = AsyncMock(return_value=feed)

    asyncio.run(visitors._update_latest_visitor())

    entry = visitors.recent[0]
    assert isinstance(entry, RecentVisitor)
    assert entry.media.content_url == "https://cdn.example.com/m1.jpg"
    assert entry.species.name == "Cardinal"
    assert entry.created_at is not None
