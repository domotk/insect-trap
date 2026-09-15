"""The button you press when you put a new refill in."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.const import EntityCategory

from .const import DOMAIN
from .entity import TrapEntity
from .tracker import TrapTracker


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    tracker: TrapTracker = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ReplaceButton(tracker), RemoveRefillButton(tracker)])


class ReplaceButton(TrapEntity, ButtonEntity):
    _attr_icon = "mdi:autorenew"

    def __init__(self, tracker: TrapTracker) -> None:
        super().__init__(tracker, "replace")

    async def async_press(self) -> None:
        await self.tracker.async_replace()


class RemoveRefillButton(TrapEntity, ButtonEntity):
    """Empty the socket again.

    The way back out of "a refill is fitted". Marked diagnostic rather than a
    control, because taking the bottle out is the rare half of the pair and it
    should not sit next to the button pressed every few weeks.
    """

    _attr_icon = "mdi:tray-remove"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, tracker: TrapTracker) -> None:
        super().__init__(tracker, "remove_refill")

    async def async_press(self) -> None:
        await self.tracker.async_remove_refill()
