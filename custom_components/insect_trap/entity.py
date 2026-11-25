"""Shared base entity: every entity belongs to the trap it tracks."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_BRAND, CONF_MODEL, CONF_SWITCH, DOMAIN
from .tracker import TrapTracker


# `via_device_id` arrived in DeviceInfo after the minimum in hacs.json; older
# releases reject a DeviceInfo carrying a key they do not know, and the entity
# never gets created. There the (domain, identifier) tuple is the only form.
_HAS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__


def _via_device(hass: HomeAssistant, source_entity: str) -> dict[str, Any]:
    """The DeviceInfo key that links the trap to the device owning the relay.

    The relay belongs to whatever integration exposes it — a Shelly plug here —
    and Home Assistant only lists an entity under its own device. Linking the
    two puts the plug one click away from the trap and makes the relationship
    visible instead of implied.

    Where Home Assistant offers it this is the registry's own device id: the
    (domain, identifier) tuple is deprecated there. Older releases only know
    the tuple, so they get one of the owning device's identifiers instead.
    """
    entity = er.async_get(hass).async_get(source_entity)
    if entity is None or entity.device_id is None:
        return {}
    # Only link to something that really exists, or the device page would point
    # at nothing.
    device = dr.async_get(hass).async_get(entity.device_id)
    if device is None:
        return {}
    if _HAS_VIA_DEVICE_ID:
        return {"via_device_id": device.id}
    if not device.identifiers:
        return {}
    return {"via_device": min(device.identifiers)}


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
            **_via_device(tracker.hass, conf.get(CONF_SWITCH, "")),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.tracker.async_add_listener(self.async_write_ha_state))
