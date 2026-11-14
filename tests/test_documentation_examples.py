"""Run documented notification branches with HA and verify translation contracts."""

import json
from pathlib import Path
import re
from typing import Any, cast

from birdbuddy.visits import normalize_visit
from homeassistant.core import SupportsResponse
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml
import pytest

from custom_components.birdbuddy.events import build_postcard_event, postcard_details
from tests.fixtures import load_fixture

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("fixture", "remove_preview", "expected_name", "images", "videos"),
    [
        ("postcard_complete.json", False, None, 2, 1),
        ("postcard_unrecognized.json", False, "Unidentified visitor", 2, 1),
        ("postcard_video_only.json", False, "Unidentified visitor", 0, 1),
        ("postcard_video_only.json", True, "Unidentified visitor", 0, 1),
    ],
)
async def test_notification_example_reads_details_and_handles_media_branches(
    hass, fixture, remove_preview, expected_name, images, videos
):
    visit = normalize_visit(load_fixture(fixture))
    detail = postcard_details(visit, config_entry_id="entry-a", account_id="account-a")
    if remove_preview:
        detail["preview_url"] = None
    event = build_postcard_event(
        visit,
        config_entry_id="entry-a",
        account_id="account-a",
        event_id="stable-event",
    )
    # Compaction never requires an unsafe inference call to retrieve details.
    event.update(medias=[], species=[], details_required=True)
    reads, notifications = [], []

    async def read(call):
        reads.append(dict(call.data))
        return detail

    async def notify(call):
        notifications.append(dict(call.data))

    hass.services.async_register(
        "birdbuddy",
        "get_postcard_details",
        read,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register("notify", "mobile_app_your_phone", notify)
    config = cast(
        dict[str, Any], load_yaml(str(ROOT / "examples/notify_postcard.yaml"))
    )
    event_filter = config["trigger"][0]["event_data"]
    event_filter.update(config_entry_id="entry-a", feeder_id=visit.feeder_id)
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    await hass.async_start()
    await hass.async_block_till_done()
    hass.bus.async_fire("birdbuddy_new_postcard", event)
    await hass.async_block_till_done()
    assert reads == [
        {
            "config_entry_id": "entry-a",
            "account_id": "account-a",
            "feeder_id": visit.feeder_id,
            "postcard_id": visit.item_id,
        }
    ]
    assert len(notifications) == 1
    notification = notifications[0]
    expected_name = expected_name or ", ".join(
        species.name for species in visit.species if species.name
    )
    assert notification["title"] == expected_name
    assert notification["message"] == f"{images} photo(s), {videos} video(s)"
    assert notification["data"]["tag"] == "stable-event"
    if detail["preview_url"]:
        assert notification["data"]["image"] == detail["preview_url"]
    else:
        assert "image" not in notification["data"]


def _leaves(data, prefix=""):
    result = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_leaves(value, path))
        else:
            result[path] = value
    return result


@pytest.mark.parametrize("language", ["en", "de", "nl", "sv"])
def test_translations_cover_every_shipped_key_and_preserve_placeholders(language):
    root = ROOT / "custom_components/birdbuddy"
    canonical = _leaves(json.loads((root / "strings.json").read_text()))
    translated = _leaves(
        json.loads((root / f"translations/{language}.json").read_text())
    )
    assert translated.keys() == canonical.keys()
    for key, text in canonical.items():
        assert isinstance(translated[key], str) and translated[key].strip()
        assert set(re.findall(r"\{[^{}]+\}", text)) == set(
            re.findall(r"\{[^{}]+\}", translated[key])
        ), key
