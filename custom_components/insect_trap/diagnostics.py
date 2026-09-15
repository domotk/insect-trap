"""Everything needed to diagnose one trap, in a single download.

Without this, working out why a trap miscounts meant asking the user to read
attributes off a screen and type them back — and the two things that matter most
are not attributes at all: the refill history, and the reference draw the mode
detector learned. Both live in the store and were invisible.

Nothing here is secret. A trap knows the id of its relay and of its power meter,
and both are already on its device page, so there is nothing to redact.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import catalog
from .const import CONF_BRAND, CONF_MODEL, DOMAIN
from .tracker import TrapTracker


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Config, tally, detector state and history for one trap."""
    tracker: TrapTracker | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    conf = {**entry.data, **entry.options}
    marca, modelo = conf.get(CONF_BRAND, ""), conf.get(CONF_MODEL, "")

    salida: dict[str, Any] = {
        "entry": {
            "version": entry.version,
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "catalogue": {
            "model_known": catalog.model_info(marca, modelo) is not None,
            "modes": catalog.modes(marca, modelo),
            "refills": catalog.refill_names(marca, modelo),
        },
    }

    if tracker is None:
        salida["tracker"] = "not loaded"
        return salida

    salida["tracker"] = {
        # what it thinks the refill is doing
        "refill_state": tracker.refill_state,
        "has_refill": tracker.has_refill,
        "percent_left": tracker.percent_left,
        "consumed_fraction": round(tracker.consumed_fraction, 4),
        "used_hours": tracker.used_hours,
        "remaining_hours": tracker.remaining_hours,
        "rated_hours": tracker.rated_hours,
        "lifetime_hours": tracker.lifetime_hours,
        "mode_factor": tracker.mode_factor,
        "warn_percent": tracker.warn_percent,
        "warned": tracker.warned,
        # how it is counting
        "source_entity": tracker.source_entity,
        "on_since": tracker.on_since.isoformat() if tracker.on_since else None,
        "running_since": (
            tracker.running_since.isoformat() if tracker.running_since else None
        ),
        "last_tick": tracker.last_tick.isoformat() if tracker.last_tick else None,
        "last_replaced": (
            tracker.last_replaced.isoformat() if tracker.last_replaced else None
        ),
        "avg_hours_per_day": tracker.avg_hours_per_day,
        # what the mode detector sees
        "auto_mode": tracker.auto_mode,
        "notify_enabled": tracker.notify_enabled,
        "power_entity": tracker.power_entity,
        "power_now": tracker.power_now,
        "power_reference": tracker.reference_power,
        "power_expected": tracker.expected_power,
        # the log the reset used to destroy
        "history": tracker.history,
    }
    return salida
