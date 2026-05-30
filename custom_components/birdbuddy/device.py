"""Bird Buddy device"""

from homeassistant.helpers.entity import DeviceInfo
from birdbuddy.feeder import Feeder, FeederDeviceVersion, FeederHousingType
from .const import DOMAIN, MANUFACTURER


# Bird Buddy's official product names for each housing form factor.
_HOUSING_NAMES: dict[FeederHousingType, str] = {
    FeederHousingType.CLASSIC: "The Birdbuddy Feeder",
    FeederHousingType.HUMMINGBIRD: "Smart Hummingbird Feeder",
    FeederHousingType.BIRD_BATH: "Smart Bird Bath",
}


class BirdBuddyDevice(Feeder):
    """Represents one Bird Buddy device"""

    @property
    def device_info(self) -> DeviceInfo:
        """The Home Assistant DeviceInfo"""
        return DeviceInfo(
            identifiers={(DOMAIN, self.id)},
            manufacturer=MANUFACTURER,
            model=_HOUSING_NAMES.get(self.housing_type, "Bird Buddy"),
            model_id=(
                self.housing_type.value
                if self.housing_type
                and self.housing_type != FeederHousingType.UNKNOWN
                else None
            ),
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
