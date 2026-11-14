"""Actionable migration notices without rewriting user automations."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, EVENT_NEW_POSTCARD_SIGHTING, LEGACY_ALIASES_REMOVE_IN


def report_legacy_postcard_contract(hass: HomeAssistant) -> None:
    """Explain why an old report-token action/listener needs deliberate migration."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        "legacy_postcard_contract",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="legacy_postcard_contract",
        learn_more_url=(
            "https://github.com/JoeQuantum/ha-birdbuddy/blob/main/"
            "docs/postcard-selection-policy.md"
        ),
    )


def report_legacy_aliases(hass: HomeAssistant) -> None:
    """Keep equivalent ID/share aliases usable during two modern minor releases."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        "deprecated_postcard_aliases",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="deprecated_postcard_aliases",
        translation_placeholders={"removal_version": LEGACY_ALIASES_REMOVE_IN},
        learn_more_url=(
            "https://github.com/JoeQuantum/ha-birdbuddy/blob/main/docs/postcard-events.md"
        ),
    )


def check_legacy_listeners(hass: HomeAssistant) -> None:
    """Inspect listeners after startup, when automations have registered them."""
    if hass.bus.async_listeners().get(EVENT_NEW_POSTCARD_SIGHTING):
        report_legacy_postcard_contract(hass)
