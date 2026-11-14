"""Bird Buddy entity helpers"""

from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import BirdBuddyDataUpdateCoordinator
from .device import BirdBuddyDevice


class BirdBuddyMixin(CoordinatorEntity[BirdBuddyDataUpdateCoordinator], RestoreEntity):
    """Helper for all Bird Buddy entities"""

    _feeder: BirdBuddyDevice
    coordinator: BirdBuddyDataUpdateCoordinator

    def __init__(
        self,
        feeder: BirdBuddyDevice,
        coordinator: BirdBuddyDataUpdateCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self.feeder = feeder
        self._attr_device_info = feeder.device_info

    @property
    def feeder(self) -> BirdBuddyDevice:
        """Use current role/capability data even before the listener runs."""
        current = self.coordinator.feeders.get(self._feeder.id)
        return current if isinstance(current, BirdBuddyDevice) else self._feeder

    @feeder.setter
    def feeder(self, feeder: BirdBuddyDevice) -> None:
        self._feeder = feeder

    def _handle_coordinator_update(self) -> None:
        if current := self.coordinator.feeders.get(self.feeder.id):
            # Reappearance can create a new runtime object; entity IDs survive.
            self.feeder = current
            self._attr_device_info = current.device_info
        return super()._handle_coordinator_update()

    @property
    def entity_registry_enabled_default(self) -> bool:
        if self.feeder.is_pending:
            # While pending, we only have access to the name and id.
            return False
        return super().entity_registry_enabled_default

    @property
    def available(self) -> bool:
        current = self.coordinator.feeders.get(self.feeder.id)
        return bool(
            super().available
            and current is not None
            and current.get("__typename") in {"FeederForOwner", "FeederForMember"}
        )
