"""Upgrade/reload an existing entry with real registries and private intake files."""

from copy import deepcopy
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from birdbuddy.client import BirdBuddy
from birdbuddy.feed import Feed
from birdbuddy.visits import normalize_visit
from homeassistant.core import CoreState
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.birdbuddy.const import CONF_ACCOUNT_ID, DOMAIN
from custom_components.birdbuddy.intakestore import ActionAlreadyRecordedError
from tests.coordinator_helpers import card, connection
from tests.fixtures import load_fixture
from tests.ha_compat import common as ha_test_common


@pytest.fixture
def hass_storage():
    """Use HA's real Store writer, not the test harness's memory replacement."""
    yield {}


@pytest.fixture
def hass_config_dir(tmp_path):
    return str(tmp_path)


async def test_upgrade_reload_and_code_rollback_interval_preserve_existing_state(
    hass, tmp_path
):
    hass.config.config_dir = str(tmp_path)
    hass.set_state(CoreState.starting)
    email = "existing@example.invalid"
    entry = ha_test_common.MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id=email,
        title="User chosen account title",
        data={"email": email, "password": "synthetic-unused", "keep_existing": True},
        options={"polling_interval": 3},
    )
    entry.add_to_hass(hass)
    original_id = entry.entry_id
    devices, entities = dr.async_get(hass), er.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "feeder-a")},
    )
    devices.async_update_device(device.id, name_by_user="User chosen device name")
    existing = {}
    for suffix in ("battery", "recent-visitor"):
        entity = entities.async_get_or_create(
            "sensor",
            DOMAIN,
            f"feeder-a-{suffix}",
            config_entry=entry,
            device_id=device.id,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
        existing[entity.entity_id] = entity.unique_id
    state_entity = entities.async_get_or_create(
        "sensor",
        DOMAIN,
        "feeder-a-state",
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="my_existing_bird_state",
    )
    existing[state_entity.entity_id] = state_entity.unique_id
    snapshot = [card("historical-baseline")]
    clients = []
    action_client = None

    def offline_client(*args):
        client = BirdBuddy(refresh_token="synthetic-unused")
        account = load_fixture("account_owner_hardware.json")
        account["user"].update(id="account-a", email=email)
        client._save_me(account)
        client.refresh = AsyncMock(return_value=True)

        async def feed(first=20, **kwargs):
            return Feed(connection(snapshot[:first]))

        async def pages(*, max_pages=100):
            yield connection(snapshot)

        async def details(item_id):
            return normalize_visit(
                next(row for row in snapshot if row["id"] == item_id)
            )

        client.feed = AsyncMock(side_effect=feed)
        client._feed_pages = pages
        client.get_feed_item = AsyncMock(side_effect=details)
        client.refresh_collections = AsyncMock(return_value={})
        client._make_request = AsyncMock(
            side_effect=AssertionError("Unexpected network")
        )
        for method in ("identify_postcard", "collect_postcard", "share_medias"):
            setattr(
                client,
                method,
                AsyncMock(side_effect=AssertionError("Unexpected mutation")),
            )
        clients.append(client)
        return client

    def assert_identity():
        assert entry.entry_id == original_id
        assert entry.unique_id == email
        assert entry.title == "User chosen account title"
        assert entry.version == 1
        assert entry.options == {"polling_interval": 3}
        assert entry.data["keep_existing"] is True
        assert entry.data[CONF_ACCOUNT_ID] == "account-a"
        retained_device = devices.async_get(device.id)
        assert retained_device is not None
        assert retained_device.name_by_user == "User chosen device name"
        for entity_id, unique_id in existing.items():
            retained = entities.async_get(entity_id)
            assert retained is not None
            assert retained.unique_id == unique_id
            assert retained.device_id == device.id
            if unique_id != "feeder-a-state":
                assert retained.disabled_by is er.RegistryEntryDisabler.USER

    with patch("custom_components.birdbuddy.BirdBuddy", side_effect=offline_client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert_identity()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator.intake.is_bootstrapped
        assert coordinator.intake.pending_count == 0
        snapshot.insert(0, card("arrived-before-restart"))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        intake = coordinator.intake
        assert intake.pending_count == 1
        async with intake.postcard_lock("uncertain-action"):
            await intake.async_begin_action(
                "uncertain-action", {"operation": "collect"}
            )
            await intake.async_finish_action("uncertain-action", "unknown")
        store_path = Path(intake._store.path)
        before = json.loads(await hass.async_add_executor_job(store_path.read_text))[
            "data"
        ]
        checkpoint = deepcopy(before["checkpoint"])
        assert (await hass.async_add_executor_job(store_path.stat)).st_mode & 0o077 == 0

        # A real entry unload must not erase the new store, even while older
        # integration code is restored separately. No legacy API is invoked.
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        persisted = await hass.async_add_executor_job(store_path.read_bytes)
        assert json.loads(persisted)["data"] == before
        assert DOMAIN not in hass.data or entry.entry_id not in hass.data[DOMAIN]
        assert_identity()

        # Re-upgrade/reload the current package against retained private state.
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert_identity()
        restored = hass.data[DOMAIN][entry.entry_id]
        assert restored.intake.pending_count == 1
        assert restored.intake.unknown_action_count == 1
        assert restored.client.export_feed_checkpoint() == checkpoint
        state = json.loads(await hass.async_add_executor_job(store_path.read_text))[
            "data"
        ]
        assert state["processed"] == before["processed"]
        assert state["pending"].keys() == before["pending"].keys()
        async with restored.intake.postcard_lock("uncertain-action"):
            with pytest.raises(ActionAlreadyRecordedError):
                await restored.intake.async_begin_action(
                    "uncertain-action", {"operation": "collect"}
                )

        # Framework unload must drain a real service task after its durable
        # action has begun, even though its mocked backend has not returned.
        entered = asyncio.Event()
        held = asyncio.Event()

        async def blocked_collection(*args, **kwargs):
            entered.set()
            await held.wait()

        action_client = restored.client
        action_client.collect_postcard = AsyncMock(side_effect=blocked_collection)
        request = asyncio.create_task(
            hass.services.async_call(
                DOMAIN,
                "collect_postcard",
                {
                    "config_entry_id": entry.entry_id,
                    "postcard_id": "arrived-before-restart",
                    "selection_policy": "server",
                    "identify": False,
                    "share": False,
                },
                blocking=True,
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert await hass.config_entries.async_unload(entry.entry_id)
            with pytest.raises(asyncio.CancelledError):
                await request
        finally:
            held.set()
            if not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
        stopped = json.loads(await hass.async_add_executor_job(store_path.read_text))[
            "data"
        ]
        assert stopped["actions"]["arrived-before-restart"]["status"] == "unknown"
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert_identity()
        assert hass.data[DOMAIN][entry.entry_id].intake.unknown_action_count == 2
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert_identity()
        assert hass.data[DOMAIN][entry.entry_id].intake.unknown_action_count == 2
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    for client in clients:
        client._make_request.assert_not_called()
        for method in ("identify_postcard", "collect_postcard", "share_medias"):
            if client is action_client and method == "collect_postcard":
                getattr(client, method).assert_awaited_once()
            else:
                getattr(client, method).assert_not_called()
