"""Insect Traps: track a refill by the hours its trap is actually on."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from . import catalog
from .const import (
    CONFIG_VERSION,
    CONF_BRAND,
    CONF_DURATION,
    CONF_DURATION_UNIT,
    CONF_LIFETIME_HOURS,
    CONF_MODE,
    CONF_MODE_FACTOR,
    CONF_FITTED_AT,
    CONF_MODEL,
    CONF_REFILL,
    CONF_RESET,
    CONF_TARGET,
    CONF_TYPE,
    CONF_WARN_PERCENT,
    DOMAIN,
    PLATFORMS,
    TARGETS,
    UNIT_HOURS,
    UNITS,
)
from .tracker import TrapTracker

_LOGGER = logging.getLogger(__name__)

# Where the card finds the catalogue and the artwork.
#
# HACS installs a card's javascript and nothing else, so anything the card needs
# beside it has to come from somewhere that every user has. Serving it from the
# integration means one copy of the catalogue — the same file Python reads — and
# no instructions about copying files into www/.
STATIC_URL = "/insect_trap/static"
# Deliberately NOT hass.data[DOMAIN]: that dict holds one tracker per entry and
# is tested for emptiness when the last entry unloads. A housekeeping key in
# there would keep it truthy for ever and the services would never be removed.
STATIC_FLAG = "insect_trap_static_paths"

SERVICE_REPLACE = "replace_refill"
SERVICE_REMOVE = "remove_refill"
SERVICE_RECALIBRATE = "recalibrate"

# Only refill data. Brand, model and mode belong to the appliance and are
# changed in the options flow: asking for them here is what made the three
# forms disagree in the first place.
SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional(CONF_REFILL): cv.string,
        vol.Optional(CONF_TARGET): vol.In(TARGETS),
        # Ranges to match what the UI already enforces. Without them a YAML
        # call with duration 0 or a negative was accepted, left the rated life
        # at zero, and the percentage silently went unknown.
        vol.Optional(CONF_DURATION): vol.All(vol.Coerce(float), vol.Range(min=1, max=10000)),
        vol.Optional(CONF_DURATION_UNIT): vol.In(UNITS),
        vol.Optional(CONF_WARN_PERCENT): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=50)
        ),
        # Correcting a typo should not cost you the count. Reset is still the
        # default, because registering a refill is what this service is for.
        vol.Optional(CONF_RESET, default=True): cv.boolean,
        vol.Optional(CONF_FITTED_AT): cv.datetime,
    }
)

REMOVE_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_id})
RECALIBRATE_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_id})

OPTIONAL_KEYS = (
    CONF_REFILL,
    CONF_TARGET,
    CONF_DURATION,
    CONF_DURATION_UNIT,
    CONF_WARN_PERCENT,
)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring an older entry up to the current schema.

    Without this, raising the flow's VERSION stops every existing entry from
    loading at all — Home Assistant refuses to set up an entry it does not know
    how to migrate, and says so only in the log.

    v1 -> v2: the appliance records what the catalogue knows about it, so the
    refill form never has to ask. Anything already stored is kept.

    v2 -> v3: the rated life is derived again instead of being stored. A stored
    copy was written once and preferred for ever, which is what turned "hours
    per night" into a control that changed nothing.

    v3 -> v4: removes what that step left behind. Its two pops were chained with
    `or`, so the key survived wherever it was not popped first.
    """
    if entry.version > CONFIG_VERSION:
        # Downgrading is not something we can do safely.
        return False

    if entry.version == 1:
        data = {**entry.data}
        brand = data.get(CONF_BRAND, "")
        model = data.get(CONF_MODEL, "")
        data.setdefault(CONF_TYPE, catalog.consumable_type(brand, model))
        data[CONF_MODE_FACTOR] = catalog.mode_factor(brand, model, data.get(CONF_MODE))
        hass.config_entries.async_update_entry(entry, data=data, version=2)
        _LOGGER.info(
            "%s: migrated to schema 2 (type=%s, mode factor=%s)",
            entry.title,
            data.get(CONF_TYPE),
            data.get(CONF_MODE_FACTOR),
        )

    if entry.version == 2:
        # v2 -> v3: the rated life is derived again instead of being stored.
        # A stored copy was written once, when a refill was registered, and then
        # preferred for ever — which is what made "hours per night" a control
        # that changed nothing. Anything already frozen is folded back into the
        # number on the box, so no entry loses its rated life on the way.
        data = {**entry.data}
        options = {**entry.options}
        # Popped from both, and deliberately not chained with `or`: that
        # short-circuits as soon as the first one yields a value, so the key
        # survived in `data` on every entry that carried it there as well.
        de_opciones = options.pop(CONF_LIFETIME_HOURS, None)
        de_datos = data.pop(CONF_LIFETIME_HOURS, None)
        stored = de_opciones or de_datos
        if stored and not (options.get(CONF_DURATION) or data.get(CONF_DURATION)):
            options[CONF_DURATION] = float(stored)
            options[CONF_DURATION_UNIT] = UNIT_HOURS
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, version=3
        )
        _LOGGER.info(
            "%s: migrated to schema 3 (rated life is derived again%s)",
            entry.title,
            f", {stored} h folded into the duration" if stored else "",
        )

    if entry.version == 3:
        # v3 -> v4: sweep up what the step above left behind while its two pops
        # were chained. Nothing reads this key any more — the rated life is
        # derived — so this is tidying, not a repair. But a stale number sitting
        # next to the live one is exactly what sends the next reader astray.
        data = {**entry.data}
        options = {**entry.options}
        sobraba = (
            data.pop(CONF_LIFETIME_HOURS, None),
            options.pop(CONF_LIFETIME_HOURS, None),
        )
        hass.config_entries.async_update_entry(
            entry, data=data, options=options, version=4
        )
        _LOGGER.info(
            "%s: migrated to schema 4%s",
            entry.title,
            " (stale rated life removed)" if any(sobraba) else "",
        )

    return True


async def _async_register_static(hass: HomeAssistant) -> None:
    """Publish the catalogue and the artwork, once per Home Assistant.

    Only these two paths: registering the package directory would put every .py
    of this integration behind an unauthenticated URL.
    """
    if hass.data.get(STATIC_FLAG):
        return
    hass.data[STATIC_FLAG] = True
    aqui = Path(__file__).parent
    try:
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(f"{STATIC_URL}/catalog.json", str(aqui / "catalog.json"), False),
                StaticPathConfig(f"{STATIC_URL}/art", str(aqui / "art"), False),
            ]
        )
    except Exception:  # noqa: BLE001 - never let the card break the integration
        hass.data[STATIC_FLAG] = False
        _LOGGER.warning(
            "Could not publish %s; the companion card will have no artwork",
            STATIC_URL,
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one trap from a config entry."""
    await _async_register_static(hass)
    tracker = TrapTracker(hass, entry)
    await tracker.async_load()
    await tracker.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = tracker
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down, saving the tally on the way out."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    tracker: TrapTracker | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if tracker is not None:
        await tracker.async_stop()
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_REPLACE)
        hass.services.async_remove(DOMAIN, SERVICE_REMOVE)
        hass.services.async_remove(DOMAIN, SERVICE_RECALIBRATE)
    return unloaded


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the user edits the appliance or the settings."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register once, no matter how many traps there are."""
    if hass.services.has_service(DOMAIN, SERVICE_REPLACE):
        return


    def _tracker_for(entity_id: str) -> tuple[TrapTracker, str]:
        """The trap behind any of its entities, or a clear refusal."""
        entity = er.async_get(hass).async_get(entity_id)
        if entity is None or entity.config_entry_id is None:
            raise ServiceValidationError(f"{entity_id} is not an insect trap entity")
        tracker: TrapTracker | None = hass.data.get(DOMAIN, {}).get(
            entity.config_entry_id
        )
        if tracker is None:
            raise ServiceValidationError(f"{entity_id} does not belong to a trap")
        return tracker, entity.config_entry_id

    async def _handle_replace(call: ServiceCall) -> None:
        tracker, entry_id = _tracker_for(call.data["entity_id"])
        reset: bool = call.data[CONF_RESET]

        changes: dict[str, Any] = {
            key: call.data[key] for key in OPTIONAL_KEYS if key in call.data
        }
        if not reset and not changes:
            raise ServiceValidationError(
                "Nothing to do: reset is off and no refill detail was given"
            )

        # Reset first and persist it, so the reload below reads the fresh tally.
        if reset:
            await tracker.async_replace(call.data.get(CONF_FITTED_AT))

        if not changes:
            return

        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return
        # The rated life is no longer stored alongside these: it is derived from
        # them every time it is asked for, so a correction here takes effect at
        # once instead of being shadowed by a frozen copy.
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, **changes}
        )
        _LOGGER.debug(
            "%s: refill updated (reset=%s): %s", entry.title, reset, changes
        )

    async def _handle_remove(call: ServiceCall) -> None:
        tracker, _ = _tracker_for(call.data["entity_id"])
        await tracker.async_remove_refill()

    async def _handle_recalibrate(call: ServiceCall) -> None:
        tracker, _ = _tracker_for(call.data["entity_id"])
        await tracker.async_recalibrate()

    hass.services.async_register(
        DOMAIN, SERVICE_REPLACE, _handle_replace, schema=SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE, _handle_remove, schema=REMOVE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RECALIBRATE, _handle_recalibrate, schema=RECALIBRATE_SCHEMA
    )
