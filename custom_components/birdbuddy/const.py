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
TRIGGER_TYPE_POSTCARD = "new_postcard"
EVENT_NEW_POSTCARD_SIGHTING = f"{DOMAIN}_new_postcard_sighting"
# Fired once per newly-seen visitor-bearing feed item (postcards, sightings,
# mystery visitors, etc). Independent of and additive to the postcard-sighting
# event above — this one carries the media URL directly, including unidentified
# postcards, and does not depend on the collect-to-sighting conversion.
EVENT_NEW_FEED_ITEM = f"{DOMAIN}_new_feed_item"

# Maximum number of recent visitors to retain for the carousel surface.
# The list is rebuilt from the feed each poll so URLs stay fresh.
RECENT_VISITOR_COUNT = 5

SERVICE_COLLECT_POSTCARD = "collect_postcard"
SERVICE_SCHEMA_COLLECT_POSTCARD = vol.Schema(
    {
        vol.Required("postcard"): cv.has_at_least_one_key("id"),
        vol.Required("sighting"): {
            vol.Required("sightingReport"): {},
            vol.Required("feeder"): vol.All(
                cv.has_at_least_one_key("id"),
                cv.has_at_least_one_key("name"),
            ),
            vol.Extra: object,
        },
        vol.Optional(CONF_DEVICE_ID): cv.string,
        vol.Optional("strategy"): cv.string,
        vol.Optional("best_guess_confidence"): vol.Coerce(int),
        vol.Optional("share_media"): vol.Coerce(bool),
    },
    extra=vol.ALLOW_EXTRA,
)
