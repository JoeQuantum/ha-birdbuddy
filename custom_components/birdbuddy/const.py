"""Constants for the Bird Buddy integration."""

from datetime import timedelta
from homeassistant.const import CONF_DEVICE_ID
from homeassistant.helpers import config_validation as cv
import logging
import voluptuous as vol

DOMAIN = "birdbuddy"
LOGGER = logging.getLogger(__package__)
MANUFACTURER = "Bird Buddy, Inc."

# Default polling interval.
# For best performance, this should be less than the access token expiration
POLLING_INTERVAL = timedelta(minutes=10)

# User-configurable polling interval (minutes), exposed via the options flow.
# Default reproduces the historical 10-minute cadence, so existing entries are
# unchanged until the user opts to override it.
CONF_POLLING_INTERVAL = "polling_interval"
DEFAULT_POLLING_INTERVAL = 10
MIN_POLLING_INTERVAL = 1
MAX_POLLING_INTERVAL = 20

CONF_FEEDER_ID = "feeder_id"
CONF_ACCOUNT_ID = "account_id"
CONF_CONFIG_ENTRY_ID = "config_entry_id"
TRIGGER_TYPE_POSTCARD = "new_postcard"
EVENT_NEW_POSTCARD_SIGHTING = f"{DOMAIN}_new_postcard_sighting"
EVENT_NEW_POSTCARD = f"{DOMAIN}_new_postcard"
EVENT_POSTCARD_BACKFILL = f"{DOMAIN}_postcard_backfill"
LEGACY_ALIASES_REMOVE_IN = "0.4.0"
# Compatibility notification event. No legacy report-token event is fabricated.
EVENT_NEW_FEED_ITEM = f"{DOMAIN}_new_feed_item"

# Maximum number of recent visitors to retain for the carousel surface.
# The list is rebuilt from the feed each poll so URLs stay fresh.
RECENT_VISITOR_COUNT = 5

SERVICE_COLLECT_POSTCARD = "collect_postcard"
SERVICE_IDENTIFY_POSTCARD = "identify_postcard"
SERVICE_GET_POSTCARD_DETAILS = "get_postcard_details"
SERVICE_BACKFILL = "backfill"
SERVICE_RETRY_FEED_ITEMS = "retry_feed_items"
SERVICE_ACKNOWLEDGE_POSTCARD_RETRY = "acknowledge_postcard_retry"

# Preserve raw values for policy validation. In particular, text "false" must
# never become True through bool coercion, nor may a default opt into a policy.
SERVICE_SCHEMA_COLLECT_POSTCARD = vol.Schema(dict)
_SERVICE_TARGETS = {
    vol.Optional(CONF_DEVICE_ID): cv.string,
    vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string,
    vol.Optional(CONF_ACCOUNT_ID): cv.string,
    vol.Optional(CONF_FEEDER_ID): cv.string,
}
SERVICE_SCHEMA_POSTCARD_ID = vol.Schema(
    {
        vol.Required("postcard_id"): vol.All(cv.string, vol.Length(min=1)),
        **_SERVICE_TARGETS,
    }
)
SERVICE_SCHEMA_BACKFILL = vol.Schema(
    {
        vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(CONF_ACCOUNT_ID): cv.string,
        vol.Optional("max_pages", default=10): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
    }
)
SERVICE_SCHEMA_RETRY_FEED_ITEMS = vol.Schema(
    {
        vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(CONF_ACCOUNT_ID): cv.string,
        vol.Optional("postcard_id"): vol.All(cv.string, vol.Length(min=1)),
    }
)
SERVICE_SCHEMA_ACKNOWLEDGE_RETRY = vol.Schema(
    {
        vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(CONF_ACCOUNT_ID): cv.string,
        vol.Required("postcard_id"): vol.All(cv.string, vol.Length(min=1)),
        vol.Required("confirm"): vol.All(bool, vol.In([True])),
    }
)
