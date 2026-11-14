"""Offline account harness using real client checkpoints and real HA storage."""

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock

from birdbuddy.client import BirdBuddy
from birdbuddy.feed import Feed
from birdbuddy.feeder import Feeder
from birdbuddy.user import BirdBuddyUser
from birdbuddy.visits import normalize_visit
from homeassistant.config_entries import ConfigEntryState
import pytest

from custom_components.birdbuddy.const import DOMAIN
from custom_components.birdbuddy.coordinator import BirdBuddyDataUpdateCoordinator
from tests.fixtures import load_fixture
from tests.ha_compat import common as ha_test_common


def card(item_id="card-a", *, feeder="feeder-a", kind="FeedItemNewPostcard"):
    raw = load_fixture("postcard_complete.json")
    raw.update(id=item_id, __typename=kind)
    raw["feeder"]["id"] = feeder
    return raw


def connection(nodes):
    return {
        "edges": [
            {"node": deepcopy(node), "cursor": str(i)} for i, node in enumerate(nodes)
        ],
        "pageInfo": {"hasNextPage": False, "endCursor": None},
    }


@pytest.fixture
def hass_storage():
    """Bypass the harness memory Store to exercise actual durable writes."""
    yield {}


@pytest.fixture
def hass_config_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture(autouse=True)
def isolated_storage_directory(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)


class AccountHarness:
    """Mock network boundaries; keep checkpoint, visitor, queue and service code real."""

    def __init__(
        self,
        hass,
        *,
        account="account-a",
        feeders=("feeder-a",),
        snapshot=(),
        entry=None,
        options=None,
    ):
        self.entry = entry or ha_test_common.MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"{account}@example.invalid",
            data={
                "email": f"{account}@example.invalid",
                "password": "fixture-password",
                "account_id": account,
            },
            options=options or {},
        )
        if entry is None:
            self.entry.add_to_hass(hass)
        self.entry.mock_state(hass, ConfigEntryState.LOADED)
        self.snapshot = list(snapshot)
        self.display_error: Exception | None = None
        self.intake_error: Exception | None = None
        self.scan_arguments = []
        self.client: Any = BirdBuddy(self.entry.data["email"], "fixture-password")
        self.client._needs_login = lambda: False
        self.client._me = BirdBuddyUser(
            {"id": account, "email": self.entry.data["email"]}
        )
        self.client._feeders = {
            fid: Feeder({"id": fid, "name": fid, "__typename": "FeederForOwner"})
            for fid in feeders
        }
        self.client.refresh = AsyncMock(return_value=True)
        self.client.feed = AsyncMock(side_effect=self._feed)
        self.client._feed_pages = self._pages
        self.client._make_request = AsyncMock(
            side_effect=AssertionError("Unexpected cloud request")
        )
        self.client.get_feed_item = AsyncMock(side_effect=self._get_item)
        self.client.refresh_collections = AsyncMock(return_value={})
        for name in (
            "identify_postcard",
            "collect_postcard",
            "share_medias",
            "sighting_from_postcard",
            "finish_postcard",
        ):
            setattr(
                self.client,
                name,
                AsyncMock(side_effect=AssertionError("Unexpected mutation")),
            )
        self.coordinator = BirdBuddyDataUpdateCoordinator(hass, self.client, self.entry)
        hass.data.setdefault(DOMAIN, {})[self.entry.entry_id] = self.coordinator

    async def _feed(self, first=20, **kwargs):
        error = self.display_error if first == 100 else self.intake_error
        if error:
            raise error
        return Feed(connection(self.snapshot[:first]))

    async def _pages(self, *, max_pages=100):
        self.scan_arguments.append(max_pages)
        if self.intake_error:
            raise self.intake_error
        for offset in range(0, len(self.snapshot), 100):
            if offset // 100 >= max_pages:
                raise ValueError("Synthetic page limit exceeded")
            yield connection(self.snapshot[offset : offset + 100])

    async def _get_item(self, item_id):
        return normalize_visit(
            next(raw for raw in self.snapshot if raw["id"] == item_id),
            known_feeder_ids=tuple(self.client.feeders),
        )

    async def poll(self):
        return await self.coordinator._async_update_data()

    def assert_no_mutations(self):
        for name in (
            "identify_postcard",
            "collect_postcard",
            "share_medias",
            "sighting_from_postcard",
            "finish_postcard",
        ):
            getattr(self.client, name).assert_not_called()


@pytest.fixture
async def accounts_factory(hass):
    accounts = []

    def make(**kwargs):
        result = AccountHarness(hass, **kwargs)
        accounts.append(result)
        return result

    yield make
    for account in accounts:
        await account.coordinator.async_shutdown()
