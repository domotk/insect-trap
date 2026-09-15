"""Shared base entity: every entity belongs to the trap it tracks."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_BRAND, CONF_MODEL, CONF_SWITCH, DOMAIN
from .tracker import TrapTracker


def _via_device_id(hass: HomeAssistant, source_entity: str) -> str | None:
    """The registry id of the device that owns the relay.

    The relay belongs to whatever integration exposes it — a Shelly plug here —
    and Home Assistant only lists an entity under its own device. Linking the
    two puts the plug one click away from the trap and makes the relationship
    visible instead of implied.

    Note this is the registry's own device id, not the (domain, identifier)
    tuple: that older form is deprecated and Home Assistant ignores it.
    """
    entity = er.async_get(hass).async_get(source_entity)
    if entity is None or entity.device_id is None:
        return None
    # Only link to something that really exists, or the device page would point
    # at nothing.
    if dr.async_get(hass).async_get(entity.device_id) is None:
        return None
    return entity.device_id


class TrapEntity(Entity):
    """Base for every entity of an insect trap."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, tracker: TrapTracker, key: str) -> None:
        self.tracker = tracker
        self._attr_translation_key = key
        self._attr_unique_id = f"{tracker.entry.entry_id}_{key}"

        conf = {**tracker.entry.data, **tracker.entry.options}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, tracker.entry.entry_id)},
            name=tracker.entry.title,
            manufacturer=conf.get(CONF_BRAND) or "Insect Traps",
            model=conf.get(CONF_MODEL) or None,
            via_device_id=_via_device_id(tracker.hass, conf.get(CONF_SWITCH, "")),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.tracker.async_add_listener(self.async_write_ha_state))
