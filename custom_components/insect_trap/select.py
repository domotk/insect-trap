"""The appliance's mode, as a control instead of a buried setting.

The mode is a physical button on the trap that the user can flip at any time,
so it belongs on the device page next to everything else — not three clicks
deep in the options dialog. Appliances without such a button get no entity at
all rather than a control with one option.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import catalog
from .const import CONF_BRAND, CONF_MODE, CONF_MODE_FACTOR, CONF_MODEL, DOMAIN
from .entity import TrapEntity
from .tracker import TrapTracker


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    tracker: TrapTracker = hass.data[DOMAIN][entry.entry_id]
    conf = {**entry.data, **entry.options}
    modes = catalog.modes(conf.get(CONF_BRAND, ""), conf.get(CONF_MODEL, ""))
    if not modes:
        return
    async_add_entities([TrapModeSelect(tracker, [m["id"] for m in modes])])


class TrapModeSelect(TrapEntity, SelectEntity):
    """Normal or boosted, whichever the button on the trap is set to."""

    _attr_icon = "mdi:speedometer"

    def __init__(self, tracker: TrapTracker, options: list[str]) -> None:
        super().__init__(tracker, "mode")
        self._attr_options = options

    @property
    def current_option(self) -> str | None:
        conf = {**self.tracker.entry.data, **self.tracker.entry.options}
        return conf.get(CONF_MODE)

    async def async_select_option(self, option: str) -> None:
        """Record the new setting and how much faster it burns a refill.

        Updating the options reloads the entry. The tracker banks the open
        period on the way out, so the hours already spent are charged at the
        old rate and only what comes next uses the new one.
        """
        entry = self.tracker.entry
        conf = {**entry.data, **entry.options}
        factor = catalog.mode_factor(
            conf.get(CONF_BRAND, ""), conf.get(CONF_MODEL, ""), option
        )
        self.hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_MODE: option, CONF_MODE_FACTOR: factor},
        )
