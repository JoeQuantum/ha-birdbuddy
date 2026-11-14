"""Exercise the imported collection blueprint through HA's automation engine."""

import asyncio
from inspect import signature
import logging
from pathlib import Path
from typing import Any, cast

from homeassistant.components import automation
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml
import pytest

from custom_components.birdbuddy.postcard_policy import validate_collection_request
from tests.ha_compat import common as ha_test_common

BLUEPRINT = (
    Path(__file__).parents[1]
    / "custom_components/birdbuddy/blueprints/collect_postcard.yaml"
)


def _substitute(inputs):
    """Use each supported HA version's own blueprint schema and substitution."""
    options: dict[str, Any] = {"expected_domain": "automation"}
    if "schema" in signature(Blueprint).parameters:
        from homeassistant.components.automation.config import (
            AUTOMATION_BLUEPRINT_SCHEMA,
        )

        options["schema"] = AUTOMATION_BLUEPRINT_SCHEMA
    data = load_yaml(str(BLUEPRINT))
    assert isinstance(data, dict)
    blueprint = Blueprint(data, **options)
    configured = BlueprintInputs(
        blueprint,
        {
            "id": "bb-queued-collection",
            "alias": "BB queued collection",
            "use_blueprint": {
                "path": "birdbuddy/collect_postcard.yaml",
                "input": inputs,
            },
        },
    )
    configured.validate()
    return configured.async_substitute()


def _actions(config) -> list[dict[str, Any]]:
    actions = config.get("actions", config.get("action"))
    assert isinstance(actions, list)
    return actions


async def _setup(hass, handler, **inputs):
    entry = ha_test_common.MockConfigEntry(
        domain="birdbuddy", unique_id="account-a", data={}
    )
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("birdbuddy", "feeder-a")},
        name="Fixture feeder",
    )
    configured = _substitute(
        {
            "config_entry_id": entry.entry_id,
            "feeder_device": device.id,
            **inputs,
        }
    )
    hass.services.async_register("birdbuddy", "collect_postcard", handler)
    assert await async_setup_component(hass, "automation", {"automation": [configured]})
    await hass.async_start()
    await hass.async_block_till_done()
    entity = hass.data[cast(Any, automation).DATA_COMPONENT].get_entity(
        "automation.bb_queued_collection"
    )
    assert entity is not None
    return entry, device, entity, configured


def _event(entry, postcard_id="card-1", **overrides):
    return {
        "schema_version": 1,
        "source": "live",
        "kind": "FeedItemNewPostcard",
        "config_entry_id": entry.entry_id,
        "account_id": "account-a",
        "feeder_id": "feeder-a",
        "postcard_id": postcard_id,
        "event_id": f"stable-{postcard_id}",
        **overrides,
    }


async def _wait_for_runs(hass, entity, count):
    ready = asyncio.Event()

    @callback
    def changed(event):
        if entity.action_script.runs == count:
            ready.set()

    unsubscribe = hass.bus.async_listen("state_changed", changed)
    try:
        changed(None)
        await asyncio.wait_for(ready.wait(), timeout=2)
    finally:
        unsubscribe()


class _OverflowSignal(logging.Handler):
    def __init__(self):
        super().__init__()
        self.event = asyncio.Event()

    def emit(self, record):
        if "maximum" in record.getMessage().lower():
            self.event.set()


async def test_blueprint_defaults_require_policy_review(hass):
    calls = []

    async def collect(call):
        calls.append(call.data)

    entry, _, _, config = await _setup(hass, collect)
    hass.bus.async_fire("birdbuddy_new_postcard", _event(entry))
    await hass.async_block_till_done()
    assert not calls
    assert config["mode"] == "queued"
    assert config["max"] == 20
    assert config["max_exceeded"].lower() == "warning"
    assert config["variables"]["selected_policy"] == "review_required"
    assert _actions(config)[-1]["data"]["identify"] is False
    assert _actions(config)[-1]["data"]["share"] is False


async def test_two_rapid_postcards_are_serialized_with_exact_targets(hass):
    calls = []
    release = asyncio.Event()
    entered = asyncio.Event()

    async def collect(call):
        validate_collection_request(call.data)
        calls.append(dict(call.data))
        entered.set()
        await release.wait()

    entry, device, entity, config = await _setup(
        hass, collect, selection_policy="server", identify=True, share=False
    )
    try:
        hass.bus.async_fire("birdbuddy_new_postcard", _event(entry, "card-1"))
        await asyncio.wait_for(entered.wait(), 2)
        hass.bus.async_fire("birdbuddy_new_postcard", _event(entry, "card-2"))
        await _wait_for_runs(hass, entity, 2)
        assert [call["postcard_id"] for call in calls] == ["card-1"]
    finally:
        release.set()
        await hass.async_block_till_done()
    assert [call["postcard_id"] for call in calls] == ["card-1", "card-2"]
    assert all(
        call["config_entry_id"] == entry.entry_id and call["device_id"] == device.id
        for call in calls
    )
    assert all(
        call["account_id"] == "account-a" and call["feeder_id"] == "feeder-a"
        for call in calls
    )
    assert all(call["identify"] is True and call["share"] is False for call in calls)
    assert not any(
        {
            "strategy",
            "best_guess_confidence",
            "sighting",
            "postcard",
            "share_media",
            "event_id",
        }
        & call.keys()
        for call in calls
    )
    assert (
        _actions(config)[0]["variables"]["birdbuddy_event_id"]
        == "{{ trigger.event.data.event_id }}"
    )


@pytest.mark.parametrize(
    "override",
    [
        {"source": "backfill"},
        {"schema_version": 2},
        {"kind": "FeedItemCollectedPostcard"},
        {"config_entry_id": "different-account"},
        {"feeder_id": "another-feeder"},
        {"event_id": None},
        {"event_id": ""},
        {"postcard_id": None},
        {"account_id": None},
    ],
)
async def test_only_supported_live_events_for_selected_account_feeder_collect(
    hass, override
):
    calls = []

    async def collect(call):
        calls.append(call.data)

    entry, _, _, _ = await _setup(hass, collect, selection_policy="server")
    hass.bus.async_fire("birdbuddy_new_postcard", _event(entry, **override))
    await hass.async_block_till_done()
    assert not calls


async def test_finite_queue_logs_overflow_and_keeps_accepted_work(hass, caplog):
    calls = []
    release = asyncio.Event()
    entered = asyncio.Event()

    async def collect(call):
        calls.append(call.data["postcard_id"])
        entered.set()
        await release.wait()

    entry, _, entity, _ = await _setup(
        hass, collect, selection_policy="server", queue_limit=2
    )
    overflow = _OverflowSignal()
    logger = logging.getLogger("homeassistant.components.automation")
    logger.addHandler(overflow)
    try:
        hass.bus.async_fire("birdbuddy_new_postcard", _event(entry, "card-1"))
        await asyncio.wait_for(entered.wait(), 2)
        hass.bus.async_fire("birdbuddy_new_postcard", _event(entry, "card-2"))
        await _wait_for_runs(hass, entity, 2)
        hass.bus.async_fire("birdbuddy_new_postcard", _event(entry, "overflow"))
        await asyncio.wait_for(overflow.event.wait(), 2)
        assert entity.action_script.runs == 2
    finally:
        logger.removeHandler(overflow)
        release.set()
        await hass.async_block_till_done()
    assert calls == ["card-1", "card-2"]
    assert any(
        record.levelname == "WARNING" and "maximum" in record.getMessage().lower()
        for record in caplog.records
    )


@pytest.mark.parametrize(
    "failure",
    [HomeAssistantError("Fixture collection failure"), asyncio.CancelledError()],
)
async def test_one_failed_or_cancelled_call_releases_the_queue(hass, failure):
    calls = []
    release = asyncio.Event()
    entered = asyncio.Event()

    async def collect(call):
        calls.append(call.data["postcard_id"])
        if len(calls) == 1:
            entered.set()
            await release.wait()
            raise failure

    entry, _, entity, _ = await _setup(hass, collect, selection_policy="server")
    try:
        hass.bus.async_fire("birdbuddy_new_postcard", _event(entry, "fails"))
        await asyncio.wait_for(entered.wait(), 2)
        hass.bus.async_fire("birdbuddy_new_postcard", _event(entry, "succeeds"))
        await _wait_for_runs(hass, entity, 2)
    finally:
        release.set()
        await hass.async_block_till_done()
    assert calls == ["fails", "succeeds"]
    assert entity.action_script.runs == 0
