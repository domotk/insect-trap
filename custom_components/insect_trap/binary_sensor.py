"""One binary sensor: this trap needs a refill.

Deliberately a single sensor rather than three. You automate on this one, and
`refill_state` tells you whether the socket is empty, the bottle is finished or
it is merely running low.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, REFILL_NONE
from .entity import TrapEntity
from .tracker import TrapTracker


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    tracker: TrapTracker = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ReplacementDueBinarySensor(tracker)])


class ReplacementDueBinarySensor(TrapEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, tracker: TrapTracker) -> None:
        super().__init__(tracker, "replacement_due")

    @property
    def is_on(self) -> bool:
        # No refill counts as a problem: an empty socket is not a healthy trap.
        return self.tracker.needs_attention

    @property
    def icon(self) -> str:
        if self.tracker.refill_state == REFILL_NONE:
            return "mdi:tray-remove"
        return "mdi:alert-circle" if self.is_on else "mdi:check-circle-outline"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "refill_state": self.tracker.refill_state,
            "depleted": self.tracker.is_depleted,
            "life_left": self.tracker.percent_left,
            "remaining_hours": self.tracker.remaining_hours,
            "nights_left": self.tracker.nights_left,
        }
