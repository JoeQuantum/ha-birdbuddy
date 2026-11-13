"""Authentication recovery must retain account and registry identity."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from birdbuddy.exceptions import AuthenticationFailedError, NoResponseError
from birdbuddy.user import BirdBuddyUser
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.birdbuddy import async_setup_entry
from custom_components.birdbuddy.const import CONF_ACCOUNT_ID, DOMAIN
from custom_components.birdbuddy.coordinator import BirdBuddyDataUpdateCoordinator
from tests.ha_compat import common as ha_test_common


def _user(
    account_id: str | None = "account-a", email: str | None = "old@example.invalid"
):
    return BirdBuddyUser({"id": account_id, "name": "Fixture User", "email": email})


@pytest.mark.parametrize(
    ("stored_id", "loaded_id", "new_id", "login_email", "profile_email", "success"),
    [
        (
            "account-a",
            None,
            "account-a",
            "new@example.invalid",
            "new@example.invalid",
            True,
        ),
        (
            "account-a",
            None,
            "account-b",
            "old@example.invalid",
            "old@example.invalid",
            False,
        ),
        (
            None,
            "account-a",
            "account-b",
            "old@example.invalid",
            "old@example.invalid",
            False,
        ),
        (
            None,
            "account-a",
            "account-a",
            "new@example.invalid",
            "new@example.invalid",
            True,
        ),
        (None, None, "account-a", "OLD@example.invalid", "old@example.invalid", True),
        (
            None,
            None,
            "account-b",
            "other@example.invalid",
            "old@example.invalid",
            False,
        ),
        (
            None,
            None,
            "account-b",
            "old@example.invalid",
            "other@example.invalid",
            False,
        ),
        (None, None, "account-a", "old@example.invalid", None, False),
    ],
)
async def test_reauth_never_rebinds_an_existing_entry(
    hass, stored_id, loaded_id, new_id, login_email, profile_email, success
):
    data = {
        "email": "old@example.invalid",
        "password": "old-password",
        "retained": True,
    }
    if stored_id:
        data[CONF_ACCOUNT_ID] = stored_id
    entry = ha_test_common.MockConfigEntry(
        domain=DOMAIN,
        unique_id="old@example.invalid",
        title="Custom account",
        data=data,
        options={"polling_interval": 3},
    )
    entry.add_to_hass(hass)
    if loaded_id:
        hass.data[DOMAIN] = {
            entry.entry_id: SimpleNamespace(
                client=SimpleNamespace(user=_user(loaded_id))
            )
        }
    original_id = entry.entry_id
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "feeder-a")}
    )
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        "feeder-a-battery",
        config_entry=entry,
        device_id=device.id,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    entity_id = entity.entity_id
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"

    with (
        patch("birdbuddy.client.BirdBuddy.refresh", new=AsyncMock(return_value=True)),
        patch(
            "birdbuddy.client.BirdBuddy.user",
            new_callable=PropertyMock,
            return_value=_user(new_id, profile_email),
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_reload",
            new=AsyncMock(return_value=True),
        ) as reload_entry,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": login_email, "password": "new-password"}
        )
        await hass.async_block_till_done()

    if success:
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.data == data | {
            "email": login_email,
            "password": "new-password",
            CONF_ACCOUNT_ID: new_id,
        }
        reload_entry.assert_awaited_once_with(original_id)
    else:
        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"base": "wrong_account"}
        assert entry.data == data
        reload_entry.assert_not_awaited()
    assert entry.entry_id == original_id
    assert entry.unique_id == "old@example.invalid"
    assert entry.title == "Custom account"
    assert entry.options == {"polling_interval": 3}
    retained_entity = er.async_get(hass).async_get(entity_id)
    assert retained_entity is not None
    assert retained_entity.unique_id == "feeder-a-battery"
    assert retained_entity.device_id == device.id
    assert retained_entity.disabled_by is er.RegistryEntryDisabler.USER


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (AuthenticationFailedError(), "invalid_auth"),
        (NoResponseError(), "cannot_connect"),
    ],
)
async def test_failed_reauth_keeps_original_credentials(hass, error, message):
    entry = ha_test_common.MockConfigEntry(
        domain=DOMAIN,
        unique_id="old@example.invalid",
        data={
            "email": "old@example.invalid",
            "password": "old-password",
            CONF_ACCOUNT_ID: "account-a",
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    with patch("birdbuddy.client.BirdBuddy.refresh", new=AsyncMock(side_effect=error)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "old@example.invalid", "password": "wrong"}
        )
    assert result["errors"] == {"base": message}
    assert entry.data["password"] == "old-password"


async def test_new_login_with_existing_account_uuid_does_not_duplicate_entry(hass):
    entry = ha_test_common.MockConfigEntry(
        domain=DOMAIN,
        unique_id="old@example.invalid",
        data={
            "email": "old@example.invalid",
            "password": "old",
            CONF_ACCOUNT_ID: "account-a",
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with (
        patch("birdbuddy.client.BirdBuddy.refresh", new=AsyncMock(return_value=True)),
        patch(
            "birdbuddy.client.BirdBuddy.user",
            new_callable=PropertyMock,
            return_value=_user("account-a", "new@example.invalid"),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "new@example.invalid", "password": "new"}
        )
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert entry.data["password"] == "old"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthenticationFailedError(), ConfigEntryAuthFailed),
        (NoResponseError(), UpdateFailed),
    ],
)
async def test_coordinator_distinguishes_auth_from_transient_failure(
    hass, error, expected
):
    entry = ha_test_common.MockConfigEntry(domain=DOMAIN, data={})
    client = SimpleNamespace(refresh=AsyncMock(side_effect=error))
    coordinator = BirdBuddyDataUpdateCoordinator(hass, client, entry)
    assert coordinator.config_entry is entry
    with pytest.raises(expected):
        await coordinator._async_update_data()


async def test_setup_auth_failure_starts_reauth_without_erasing_configuration(hass):
    data = {
        "email": "old@example.invalid",
        "password": "expired",
        CONF_ACCOUNT_ID: "account-a",
    }
    entry = ha_test_common.MockConfigEntry(
        domain=DOMAIN, unique_id="old@example.invalid", data=data
    )
    entry.add_to_hass(hass)
    with patch(
        "birdbuddy.client.BirdBuddy.refresh",
        new=AsyncMock(side_effect=AuthenticationFailedError()),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.data == data
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    flows = hass.config_entries.flow.async_progress()
    assert any(
        flow["context"].get("entry_id") == entry.entry_id
        and flow["context"]["source"] == config_entries.SOURCE_REAUTH
        for flow in flows
    )


@pytest.mark.parametrize(
    ("stored_id", "new_id", "success"),
    [(None, "account-a", True), ("account-a", "account-b", False)],
)
async def test_setup_persists_identity_without_rebinding(
    hass, stored_id, new_id, success
):
    data = {"email": "old@example.invalid", "password": "password"}
    if stored_id:
        data[CONF_ACCOUNT_ID] = stored_id
    entry = ha_test_common.MockConfigEntry(
        domain=DOMAIN, unique_id="old@example.invalid", data=data
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.birdbuddy.BirdBuddy",
            return_value=SimpleNamespace(user=_user(new_id)),
        ),
        patch(
            "custom_components.birdbuddy.BirdBuddyDataUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new=AsyncMock(),
        ),
    ):
        if success:
            assert await async_setup_entry(hass, entry)
            assert entry.data[CONF_ACCOUNT_ID] == new_id
        else:
            with pytest.raises(ConfigEntryAuthFailed):
                await async_setup_entry(hass, entry)
            assert entry.data == data
            assert entry.entry_id not in hass.data[DOMAIN]
    assert entry.unique_id == "old@example.invalid"


@pytest.mark.parametrize("profile", [None, _user(None), _user("")])
async def test_setup_retries_missing_account_identity(hass, profile):
    entry = ha_test_common.MockConfigEntry(
        domain=DOMAIN,
        data={"email": "old@example.invalid", "password": "password"},
    )
    entry.add_to_hass(hass)
    original_data = dict(entry.data)
    with (
        patch(
            "custom_components.birdbuddy.BirdBuddy",
            return_value=SimpleNamespace(user=profile),
        ),
        patch(
            "custom_components.birdbuddy.BirdBuddyDataUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new=AsyncMock(),
        ) as setup_platforms,
        pytest.raises(ConfigEntryNotReady, match="no account identity"),
    ):
        await async_setup_entry(hass, entry)
    setup_platforms.assert_not_awaited()
    assert entry.data == original_data
    assert entry.entry_id not in hass.data[DOMAIN]


@pytest.mark.parametrize("stage", ["feed_events", "visitors", "postcard_media"])
@pytest.mark.parametrize("auth_error", [True, False])
async def test_secondary_polling_auth_errors_are_not_hidden(hass, stage, auth_error):
    """Optional data failures may be tolerated, expired credentials require recovery."""
    entry = ha_test_common.MockConfigEntry(domain=DOMAIN, data={})
    client = MagicMock()
    client.refresh = AsyncMock(return_value=True)
    client.refresh_feed = AsyncMock(return_value=[])
    client.feeders = {"feeder-a": {"id": "feeder-a", "name": "Fixture feeder"}}
    coordinator = BirdBuddyDataUpdateCoordinator(hass, client, entry)
    error = AuthenticationFailedError() if auth_error else NoResponseError()
    if stage == "feed_events":
        coordinator.first_update = False
        coordinator._fire_new_feed_item_events = AsyncMock(side_effect=error)
        operation = coordinator._async_update_data
    elif stage == "visitors":
        coordinator.visitors["feeder-a"] = MagicMock(
            async_update=AsyncMock(side_effect=error)
        )
        operation = coordinator._async_update_data
    else:
        client._make_request = AsyncMock(side_effect=error)
        operation = coordinator._fetch_new_postcard_media_map

    if auth_error:
        expected = (
            AuthenticationFailedError
            if stage == "postcard_media"
            else ConfigEntryAuthFailed
        )
        with pytest.raises(expected):
            await operation()
    else:
        result = await operation()
        assert result == ({} if stage == "postcard_media" else client)
