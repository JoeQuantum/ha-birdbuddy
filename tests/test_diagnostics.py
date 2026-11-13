"""Diagnostic allowlist excludes private fields, including future API additions."""

from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock

from custom_components.birdbuddy.const import DOMAIN
from custom_components.birdbuddy.diagnostics import async_get_config_entry_diagnostics
from tests.ha_compat import common as ha_test_common


async def test_diagnostics_never_serializes_private_state(hass):
    secrets = [
        "secret@example.invalid",
        "secret-password",
        "secret-account",
        "secret-feeder",
        "secret-report",
        "secret-url",
        "secret-city",
    ]
    entry = ha_test_common.MockConfigEntry(
        domain=DOMAIN,
        data={"email": secrets[0], "password": secrets[1], "account_id": secrets[2]},
    )
    client = MagicMock()
    client.export_feed_checkpoint.return_value = {
        "watermark": "2026-01-01",
        "seen": {"private-item": "private-time"},
    }
    coordinator = SimpleNamespace(
        client=client,
        feeders={
            secrets[3]: {
                "__typename": "FeederForOwner",
                "reportToken": secrets[4],
                "url": secrets[5],
                "location": secrets[6],
            }
        },
        last_update_success=True,
        last_update_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_feed_success=None,
        feed_error_type="ExceptionSecret",
        intake=SimpleNamespace(
            initialized=True,
            is_bootstrapped=True,
            pending_count=3,
            dead_letter_count=1,
            unknown_action_count=2,
        ),
    )
    hass.data[DOMAIN] = {entry.entry_id: coordinator}
    with patch(
        "custom_components.birdbuddy.diagnostics.async_get_integration",
        new=AsyncMock(return_value=SimpleNamespace(version="0.2.0")),
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)
    encoded = json.dumps(result)
    assert len(encoded) < 2000
    assert all(
        secret not in encoded
        for secret in [
            *secrets,
            "private-item",
            "private-time",
            entry.entry_id,
            "ExceptionSecret",
        ]
    )
    assert result["telemetry"]["roles"] == {"owner": 1}
    assert result["postcards"]["error_category"] == "other"
    assert result["postcards"]["unknown_action_count"] == 2
    assert result["postcards"]["checkpoint_id_count"] == 1


async def test_unloaded_account_has_version_only_diagnostics(hass):
    entry = ha_test_common.MockConfigEntry(domain=DOMAIN, data={"password": "private"})
    with patch(
        "custom_components.birdbuddy.diagnostics.async_get_integration",
        new=AsyncMock(return_value=SimpleNamespace(version="0.2.0")),
    ):
        result = await async_get_config_entry_diagnostics(hass, entry)
    assert set(result) == {"versions", "loaded"}
    assert result["loaded"] is False
