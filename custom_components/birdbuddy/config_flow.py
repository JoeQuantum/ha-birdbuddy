"""Config flow for Bird Buddy integration."""

from __future__ import annotations

from birdbuddy.client import BirdBuddy
from birdbuddy.exceptions import (
    AuthenticationFailedError,
    SocialAuthenticationRequiredError,
)
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_EMAIL
from homeassistant.core import callback

from .const import (
    CONF_ACCOUNT_ID,
    CONF_POLLING_INTERVAL,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
    MAX_POLLING_INTERVAL,
    MIN_POLLING_INTERVAL,
)
from .hass_util import _account_id_for_entry


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bird Buddy."""

    VERSION = 1

    def __init__(self) -> None:
        self._client: BirdBuddy | None = None
        super().__init__()

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Return the options flow for adjusting the polling interval."""
        return OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}
        if user_input is not None:
            result = await self._async_auth_or_validate(user_input, errors)
            if result is not None:
                if any(
                    _account_id_for_entry(self.hass, entry) == result[CONF_ACCOUNT_ID]
                    for entry in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=result["title"],
                    data=user_input | {CONF_ACCOUNT_ID: result[CONF_ACCOUNT_ID]},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Recover credentials for the existing config entry, never a new one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Authenticate and prove the credentials still refer to this account."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            expected_account_id = _account_id_for_entry(self.hass, entry)
            result = await self._async_auth_or_validate(user_input, errors)
            if result is not None:
                if expected_account_id is not None:
                    same_account = result[CONF_ACCOUNT_ID] == expected_account_id
                else:
                    # Old entries may have no UUID and no loaded client. Do not
                    # trust only the email the user typed: the authenticated
                    # profile must also report the exact existing login email.
                    current_email = entry.data[CONF_EMAIL].lower()
                    same_account = (
                        user_input[CONF_EMAIL].lower() == current_email
                        and result[CONF_EMAIL].lower() == current_email
                    )
                if not same_account:
                    errors["base"] = "wrong_account"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates=user_input
                        | {CONF_ACCOUNT_ID: result[CONF_ACCOUNT_ID]},
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=entry.data[CONF_EMAIL]): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def _async_auth_or_validate(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> dict[str, str] | None:
        client = BirdBuddy(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
        self._client = client
        try:
            result = await client.refresh()
        except SocialAuthenticationRequiredError:
            self._client = None
            errors["base"] = "social_auth_unsupported"
            return None
        except AuthenticationFailedError:
            self._client = None
            errors["base"] = "invalid_auth"
            return None
        except Exception:
            self._client = None
            errors["base"] = "cannot_connect"
            return None
        if not result:
            self._client = None
            errors["base"] = "cannot_connect"
            return None
        user = client.user
        if user is None or not isinstance(user.get("id"), str) or not user["id"]:
            errors["base"] = "cannot_connect"
            return None
        profile_email = user.get(CONF_EMAIL)
        return {
            "title": user.name,
            CONF_ACCOUNT_ID: user["id"],
            CONF_EMAIL: profile_email if isinstance(profile_email, str) else "",
        }


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Bird Buddy options (polling interval)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the polling-interval option."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_POLLING_INTERVAL, default=current): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_POLLING_INTERVAL, max=MAX_POLLING_INTERVAL),
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
