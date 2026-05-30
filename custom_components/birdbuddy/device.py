"""Bird Buddy device"""

from homeassistant.helpers.entity import DeviceInfo
from birdbuddy.feeder import Feeder, FeederDeviceVersion
from .const import DOMAIN, MANUFACTURER


# V1 is the original Bird Buddy; V1_PRO and V2 friendly names are inferred
# from Bird Buddy marketing and may need adjustment when official naming
# is confirmed.
_MODEL_NAMES: dict[FeederDeviceVersion, str] = {
    FeederDeviceVersion.V1: "Bird Buddy",
    FeederDeviceVersion.V1_PRO: "Bird Buddy Pro",
    FeederDeviceVersion.V2: "Bird Buddy 2",
}


class BirdBuddyDevice(Feeder):
    """Represents one Bird Buddy device"""

    @property
    def device_info(self) -> DeviceInfo:
        """The Home Assistant DeviceInfo"""
        return DeviceInfo(
            identifiers={(DOMAIN, self.id)},
            manufacturer=MANUFACTURER,
            model=_MODEL_NAMES.get(self.device_version, "Bird Buddy"),
            name=self.name,
            sw_version=self.get("firmwareVersion", None),
            hw_version=(
                self.device_version.value
                if self.device_version
                and self.device_version != FeederDeviceVersion.UNKNOWN
                else None
            ),
            suggested_area="Outside",
        )
