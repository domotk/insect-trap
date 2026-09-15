"""Sensors: how much is used, how much is left, and when it will run out."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_AVG_HOURS_PER_DAY,
    ATTR_BRAND,
    ATTR_LAST_REPLACED,
    ATTR_LIFETIME_HOURS,
    ATTR_MODE,
    ATTR_MODEL,
    ATTR_POWER_ENTITY,
    ATTR_REFILL,
    ATTR_REFILL_STATE,
    ATTR_REMAINING_HOURS,
    ATTR_TARGET,
    ATTR_TYPE,
    ATTR_USED_HOURS,
    CONF_BRAND,
    CONF_DURATION,
    CONF_DURATION_UNIT,
    CONF_HOURS_PER_NIGHT,
    CONF_MODE,
    CONF_MODEL,
    CONF_REFILL,
    CONF_SWITCH,
    CONF_TARGET,
    CONF_TYPE,
    DEFAULT_HOURS_PER_NIGHT,
    DEFAULT_UNIT,
    DOMAIN,
    REFILL_DEPLETED,
    REFILL_NONE,
)
from .entity import TrapEntity
from .tracker import TrapTracker


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    tracker: TrapTracker = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            LifeLeftSensor(tracker),
            UsedHoursSensor(tracker),
            RemainingHoursSensor(tracker),
            NightsLeftSensor(tracker),
            DepletionSensor(tracker),
            LastReplacedSensor(tracker),
            RefillsLoggedSensor(tracker),
        ]
    )


class LifeLeftSensor(TrapEntity, SensorEntity):
    """Percentage left. The primary sensor: carries everything in attributes.

    With no refill fitted it reports unknown rather than 100 %, because a trap
    with an empty socket is not full — and a comfortable lie here is one the
    user cannot see through until the mosquitoes tell them.
    """

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, tracker: TrapTracker) -> None:
        super().__init__(tracker, "life_left")

    @property
    def native_value(self) -> float | None:
        return self.tracker.percent_left

    @property
    def icon(self) -> str:
        state = self.tracker.refill_state
        if state == REFILL_NONE:
            return "mdi:battery-off-outline"
        if state == REFILL_DEPLETED:
            return "mdi:battery-alert-variant-outline"
        pct = self.tracker.percent_left
        if pct is None:
            return "mdi:help-circle-outline"
        if pct <= self.tracker.warn_percent:
            return "mdi:battery-low"
        if pct >= 90:
            return "mdi:battery-high"
        return "mdi:battery-medium"

    @property
    def extra_state_attributes(self) -> dict:
        conf = {**self.tracker.entry.data, **self.tracker.entry.options}
        estimate = self.tracker.depletion_estimate
        last = self.tracker.last_replaced
        return {
            # the appliance
            ATTR_BRAND: conf.get(CONF_BRAND),
            ATTR_MODEL: conf.get(CONF_MODEL),
            ATTR_MODE: conf.get(CONF_MODE),
            ATTR_TYPE: conf.get(CONF_TYPE),
            "mode_factor": self.tracker.mode_factor,
            # the refill
            ATTR_REFILL: conf.get(CONF_REFILL),
            ATTR_REFILL_STATE: self.tracker.refill_state,
            ATTR_TARGET: conf.get(CONF_TARGET),
            # What the box says and in which unit, so a card can offer a new
            # refill without having to guess how we arrived at the hours.
            CONF_DURATION: conf.get(CONF_DURATION),
            CONF_DURATION_UNIT: conf.get(CONF_DURATION_UNIT) or DEFAULT_UNIT,
            "hours_per_night": conf.get(CONF_HOURS_PER_NIGHT)
            or DEFAULT_HOURS_PER_NIGHT,
            "rated_hours": self.tracker.rated_hours,
            ATTR_LIFETIME_HOURS: self.tracker.lifetime_hours,
            # the tally
            # Which meter the mode detector watches. The readings themselves —
            # the live draw, the learned reference and what the declared mode
            # should be drawing — are deliberately NOT here: they move every
            # minute, and the recorder rewrites the whole attribute bundle
            # whenever any one of them changes. They are in the diagnostics
            # download, and the live watts are already their own entity.
            ATTR_POWER_ENTITY: self.tracker.power_entity,
            ATTR_USED_HOURS: self.tracker.used_hours,
            ATTR_REMAINING_HOURS: self.tracker.remaining_hours,
            ATTR_AVG_HOURS_PER_DAY: self.tracker.avg_hours_per_day,
            # Whether the two figures below were measured on this trap or taken
            # from the box, so a card can tell a reading from an assumption.
            "pace_basis": self.tracker.pace_basis,
            "nights_left": self.tracker.nights_left,
            "depletion_estimate": estimate.isoformat() if estimate else None,
            ATTR_LAST_REPLACED: last.isoformat() if last else None,
            "source_entity": conf.get(CONF_SWITCH),
            "running": self.tracker.on_since is not None,
            "warn_percent": self.tracker.warn_percent,
        }


class UsedHoursSensor(TrapEntity, SensorEntity):
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:timer-sand"

    def __init__(self, tracker: TrapTracker) -> None:
        super().__init__(tracker, "used_hours")

    @property
    def native_value(self) -> float:
        return round(self.tracker.used_hours, 2)

    @property
    def last_reset(self) -> datetime | None:
        """When this total last went back to zero.

        A TOTAL without one told the statistics engine the counter only ever
        climbs, so every refill looked like a meter running backwards. It resets
        when a refill is fitted, and that is exactly `last_replaced`.
        """
        return self.tracker.last_replaced


class RemainingHoursSensor(TrapEntity, SensorEntity):
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-sand-complete"

    def __init__(self, tracker: TrapTracker) -> None:
        super().__init__(tracker, "remaining_hours")

    @property
    def native_value(self) -> float | None:
        return self.tracker.remaining_hours


class NightsLeftSensor(TrapEntity, SensorEntity):
    """Nights left: at this room's own pace once proven, at the box's until then.

    `pace_basis` on the life-left sensor says which of the two you are reading.
    """

    _attr_icon = "mdi:weather-night"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, tracker: TrapTracker) -> None:
        super().__init__(tracker, "nights_left")

    @property
    def native_value(self) -> float | None:
        return self.tracker.nights_left


class DepletionSensor(TrapEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-alert"

    def __init__(self, tracker: TrapTracker) -> None:
        super().__init__(tracker, "depletion_estimate")

    @property
    def native_value(self) -> datetime | None:
        return self.tracker.depletion_estimate


class RefillsLoggedSensor(TrapEntity, SensorEntity):
    """How many refills this trap has finished, and how the last one went.

    Deliberately a count with the detail in its attributes rather than the whole
    log: the history belongs in the diagnostics download, not in a state the
    recorder keeps a copy of every time anything changes.
    """

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:history"

    def __init__(self, tracker: TrapTracker) -> None:
        super().__init__(tracker, "refills_logged")

    @property
    def native_value(self) -> int:
        return self.tracker.refills_closed

    @property
    def extra_state_attributes(self) -> dict:
        latest = self.tracker.last_refill
        if not latest:
            return {}
        return {
            "last_refill": latest.get("refill"),
            "last_refill_hours": latest.get("used_hours"),
            "last_refill_days": latest.get("days"),
            "last_refill_fitted": latest.get("fitted"),
            "last_refill_closed": latest.get("closed"),
        }


class LastReplacedSensor(TrapEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:calendar-check"

    def __init__(self, tracker: TrapTracker) -> None:
        super().__init__(tracker, "last_replaced")

    @property
    def native_value(self) -> datetime | None:
        return self.tracker.last_replaced
