"""Discover new runtime feeders while preserving Home Assistant registry entries."""

from collections.abc import Callable, Sequence

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BirdBuddyDataUpdateCoordinator
from .device import BirdBuddyDevice
from .entity import BirdBuddyMixin

FeederEntityFactory = Callable[[BirdBuddyDevice], BirdBuddyMixin | None]


@callback
def async_register_feeder_entities(
    entry: ConfigEntry,
    coordinator: BirdBuddyDataUpdateCoordinator,
    async_add_entities: AddEntitiesCallback,
    factories: Sequence[FeederEntityFactory],
) -> None:
    """Add unseen identities on successful snapshots and clean up on unload.

    Existing entities remain registered when membership disappears. Their
    availability follows the coordinator and they rebind on reappearance.
    Registry IDs, user-disabled settings and manually removed devices are not
    deleted or recreated during a discovery session.
    """
    seen_factories: set[tuple[str, int]] = set()
    seen_ids: set[str] = set()

    @callback
    def discover() -> None:
        if not coordinator.last_update_success:
            return
        entities = []
        for feeder in tuple(coordinator.feeders.values()):
            if feeder.get("__typename") not in {"FeederForOwner", "FeederForMember"}:
                continue
            for index, factory in enumerate(factories):
                key = (feeder.id, index)
                if key in seen_factories:
                    continue
                if (entity := factory(feeder)) is None:
                    continue
                seen_factories.add(key)
                if not entity.unique_id or entity.unique_id in seen_ids:
                    continue
                seen_ids.add(entity.unique_id)
                entities.append(entity)
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(discover))
    discover()
