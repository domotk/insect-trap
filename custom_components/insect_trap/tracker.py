"""Runtime accounting for one trap's refill, and what its power draw reveals.

Why this is an integration and not a template sensor: the recorder only keeps
10 days of history by default, while a refill can easily last a month. Counting
from history would silently lose hours. So we keep our own tally, persisted in
.storage, and we never depend on the recorder.

**Consumption is stored as a fraction of the bottle, not as hours.** The mode
button can be flipped at any time, and a boosted mode empties the same bottle
faster. If we counted hours against a fixed total, switching to Max after 100 of
360 hours would claim 76 hours left, when the truth is that 28 % of the bottle
is gone and the remaining 72 % lasts about 127 hours in the new mode. Adding a
fraction per tick, at whatever rate applies right then, gets that right for
free. Hours are still tallied, but only to show "hours used".

**The mode is also read back from the power draw.** Nothing tells Home
Assistant that a physical button moved, and the cost of not noticing is real:
these traps ran for days in Max while the count assumed Normal. Each mode draws
a different and very steady amount, so a sustained reading gives it away. Two
cautions are built in: the warm-up right after switching on reads high and is
ignored, and a reading that falls between two levels is reported rather than
forced into a decision.

Accounting rules:
  * switch turns on  -> remember when.
  * switch turns off -> add the elapsed period to the tally.
  * every minute while on -> accrue and persist, so a crash or a power cut
    costs a minute at most.
  * on startup, if the device was on and still is, we keep counting from the
    original timestamp. That deliberately counts the time Home Assistant was
    down: the refill really did evaporate while we were not looking.
  * on startup, if the device was on but is off now, we close the period at the
    last heartbeat, which is the best estimate we have.
  * with no refill fitted, nothing is accrued and the trap reports "none"
    rather than a comfortable but false 100 %.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import catalog
from .texts import t
from .const import (
    CONF_AUTO_MODE,
    CONF_BRAND,
    CONF_DURATION,
    CONF_DURATION_UNIT,
    CONF_HOURS_PER_NIGHT,
    CONF_MODE,
    CONF_MODE_FACTOR,
    CONF_MODEL,
    CONF_NOTIFY,
    CONF_POWER_ENTITY,
    CONF_REFILL,
    CONF_SWITCH,
    CONF_WARN_PERCENT,
    DEFAULT_HOURS_PER_NIGHT,
    DEFAULT_AUTO_MODE,
    DEFAULT_DURATION,
    DEFAULT_MODE_FACTOR,
    DEFAULT_NOTIFY,
    DEFAULT_UNIT,
    DEFAULT_WARN_PERCENT,
    DOMAIN,
    EVENT_DEPLETED,
    HISTORY_LIMIT,
    EVENT_LOW,
    EVENT_MODE_DETECTED,
    EVENT_NO_REFILL,
    POWER_CLEAR_FAR,
    POWER_CLEAR_NEAR,
    POWER_DOUBT,
    POWER_MIN_SAMPLES,
    POWER_REFERENCE_ALPHA,
    POWER_WARMUP,
    POWER_WINDOW,
    REFILL_ACTIVE,
    REFILL_DEPLETED,
    REFILL_NONE,
    STORAGE_VERSION,
    TICK_INTERVAL,
    effective_lifetime_hours,
    lifetime_hours as compute_lifetime,
)

_LOGGER = logging.getLogger(__name__)

OFF_STATES = {"off", "unavailable", "unknown", "none", ""}
UNKNOWN_STATES = {"unavailable", "unknown", "none", ""}


class TrapTracker:
    """Keeps the tally for one trap and notifies its entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[Callable[[], None]] = []

        self.used_seconds: float = 0.0
        self.consumed: float = 0.0          # fraction of the bottle, 0..1+
        self.has_refill: bool = False
        # Two different things, and conflating them broke the mode detector:
        # `on_since` is the accounting cursor, moved forward every time a
        # stretch is banked, while `running_since` is when the trap actually
        # came on. The warm-up has to measure against the latter.
        self.on_since: datetime | None = None
        self.running_since: datetime | None = None
        self.last_tick: datetime | None = None
        self.last_replaced: datetime | None = None
        # Which warning has already been given for the refill in the socket, so
        # a threshold crossed once does not announce itself every minute for the
        # rest of the bottle. Persisted, or a restart would announce it again.
        self.warned: str | None = None   # None | "low" | "depleted"
        # Finished refills, oldest first. Written only when one is replaced or
        # taken out, so "how long did the last one last" survives the reset that
        # used to wipe the only copy of the answer.
        self.history: list[dict[str, Any]] = []
        # Counted separately from the log. The log is capped, so its length
        # stopped being the number of refills the moment the cap was reached —
        # a TOTAL_INCREASING sensor that quietly freezes at 20 is worse than no
        # sensor at all.
        self.refills_closed: int = 0
        # The bottle `consumed` is a fraction OF. Set when a refill is fitted and
        # only ever moved by an explicit rescale, so that correcting the figure
        # on the box can be told apart from flipping the mode button.
        self.rated_basis: float | None = None

        # mode detection
        self.reference_power: float | None = None   # normalised to ratio 1.0
        self.power_entity: str | None = None
        self._samples: list[tuple[datetime, float]] = []
        # Whether the "this draw does not match the mode" notice is already
        # standing. Without it the judgement re-runs every tick once the window
        # is full, writing one warning a minute for as long as the mismatch
        # lasts. Not persisted: saying it once more after a restart is right.
        self._doubt_raised = False

    # --- configuration -------------------------------------------------

    @property
    def _conf(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    @property
    def source_entity(self) -> str:
        return self._conf.get(CONF_SWITCH, "")

    @property
    def mode_factor(self) -> float:
        return float(self._conf.get(CONF_MODE_FACTOR) or DEFAULT_MODE_FACTOR)

    @property
    def rated_hours(self) -> float:
        """Hours the refill lasts at the appliance's normal setting.

        Always derived, never read from a stored copy. A stored copy used to win
        over this calculation and was frozen the moment a refill was registered,
        which quietly turned "hours per night" into a control that changed
        nothing at all once the integration was actually in use.
        """
        conf = self._conf
        # Falls back to the industry's usual figure rather than to zero. A zero
        # here is not "unknown", it is a trap that counts nothing and cannot say
        # why: the percentage goes unknown, the tally never moves and no warning
        # can fire. Better a wrong number the user can see and correct.
        return compute_lifetime(
            conf.get(CONF_DURATION) or DEFAULT_DURATION,
            conf.get(CONF_DURATION_UNIT) or DEFAULT_UNIT,
            conf.get(CONF_HOURS_PER_NIGHT) or DEFAULT_HOURS_PER_NIGHT,
        )

    @property
    def lifetime_hours(self) -> float:
        """Hours it lasts in the mode the trap is set to right now."""
        return effective_lifetime_hours(self.rated_hours, self.mode_factor)

    @property
    def auto_mode(self) -> bool:
        """Whether a confident reading may rewrite the mode by itself.

        Off, the detector still watches and still says what it sees; it simply
        stops editing the user's configuration behind their back.
        """
        return bool(self._conf.get(CONF_AUTO_MODE, DEFAULT_AUTO_MODE))

    @property
    def notify_enabled(self) -> bool:
        """Whether to raise persistent notifications. Events fire either way."""
        return bool(self._conf.get(CONF_NOTIFY, DEFAULT_NOTIFY))

    @property
    def warn_percent(self) -> float:
        return float(self._conf.get(CONF_WARN_PERCENT) or DEFAULT_WARN_PERCENT)

    # --- derived values -------------------------------------------------

    @property
    def _pending_fraction(self) -> float:
        """What the currently open period has consumed but not yet stored."""
        if self.on_since is None or not self.has_refill:
            return 0.0
        hours = (dt_util.utcnow() - self.on_since).total_seconds() / 3600
        life = self.lifetime_hours
        return hours / life if life else 0.0

    @property
    def consumed_fraction(self) -> float:
        return self.consumed + self._pending_fraction

    @property
    def used_hours(self) -> float:
        total = self.used_seconds
        if self.on_since is not None:
            total += (dt_util.utcnow() - self.on_since).total_seconds()
        return round(total / 3600, 3)

    @property
    def refill_state(self) -> str:
        if not self.has_refill:
            return REFILL_NONE
        return REFILL_DEPLETED if self.consumed_fraction >= 1.0 else REFILL_ACTIVE

    @property
    def percent_left(self) -> float | None:
        """None when there is no refill: a trap with an empty socket is not 100 %."""
        if not self.has_refill or not self.lifetime_hours:
            return None
        return round(max(0.0, min(100.0, 100 * (1 - self.consumed_fraction))), 1)

    @property
    def remaining_hours(self) -> float | None:
        if not self.has_refill:
            return None
        left = self.lifetime_hours * max(0.0, 1 - self.consumed_fraction)
        return round(left, 2)

    @property
    def is_depleted(self) -> bool:
        return self.has_refill and self.consumed_fraction >= 1.0

    @property
    def needs_attention(self) -> bool:
        """No refill, an empty one, or one about to run out."""
        if not self.has_refill or self.is_depleted:
            return True
        pct = self.percent_left
        return pct is not None and pct <= self.warn_percent

    @property
    def last_refill(self) -> dict[str, Any] | None:
        """The most recently finished refill, if there has been one."""
        return self.history[-1] if self.history else None

    @property
    def days_elapsed(self) -> float:
        if self.last_replaced is None:
            return 0.0
        return max((dt_util.utcnow() - self.last_replaced).total_seconds() / 86400, 0.0)

    @property
    def avg_hours_per_day(self) -> float | None:
        """Real usage so far. Needs a little history to mean anything."""
        days = self.days_elapsed
        if days < 0.5 or self.used_hours <= 0:
            return None
        return round(self.used_hours / days, 2)

    @property
    def nights_left(self) -> float | None:
        """Nights left at *your* pace, not at the 8 h the box assumes."""
        avg = self.avg_hours_per_day
        left = self.remaining_hours
        if not avg or left is None:
            return None
        return round(left / avg, 1)

    @property
    def depletion_estimate(self) -> datetime | None:
        avg = self.avg_hours_per_day
        left = self.remaining_hours
        if not avg or left is None:
            return None
        # Rounded to the quarter hour. Unrounded, this moves every single tick
        # simply because `utcnow()` did, and the recorder stores a new row for a
        # value whose real information changes about once a day.
        ends = dt_util.utcnow() + timedelta(days=left / avg)
        return ends.replace(minute=ends.minute // 15 * 15, second=0, microsecond=0)

    # --- mode detection: readings ---------------------------------------

    @property
    def power_now(self) -> float | None:
        """The live draw, or None when there is no meter or no reading."""
        if not self.power_entity:
            return None
        state = self.hass.states.get(self.power_entity)
        if state is None or state.state in UNKNOWN_STATES:
            return None
        try:
            return round(float(state.state), 2)
        except (TypeError, ValueError):
            return None

    def _ratios(self) -> dict[str, float]:
        conf = self._conf
        return catalog.power_ratios(conf.get(CONF_BRAND, ""), conf.get(CONF_MODEL, ""))

    def _declared_ratio(self) -> float:
        conf = self._conf
        return catalog.mode_power_ratio(
            conf.get(CONF_BRAND, ""), conf.get(CONF_MODEL, ""), conf.get(CONF_MODE)
        )

    @property
    def expected_power(self) -> float | None:
        """What the declared mode should be drawing, once we have a reference."""
        if self.reference_power is None:
            return None
        return round(self.reference_power * self._declared_ratio(), 2)

    def _find_power_entity(self) -> str | None:
        """The power meter of whatever device the relay belongs to.

        The plug that switches the trap is usually also what measures it, so
        normally there is nothing to configure: find the relay's device, then
        the power sensor on it. When that guess is wrong — a plug that reports
        no power, a meter on a different device — the setting takes over, which
        used to be impossible without editing storage by hand.
        """
        chosen = (self._conf.get(CONF_POWER_ENTITY) or "").strip()
        if chosen:
            return chosen
        if not self.source_entity:
            return None
        registry = er.async_get(self.hass)
        entity = registry.async_get(self.source_entity)
        if entity is None or entity.device_id is None:
            return None
        if dr.async_get(self.hass).async_get(entity.device_id) is None:
            return None
        for candidate in er.async_entries_for_device(registry, entity.device_id):
            if candidate.device_class == SensorDeviceClass.POWER or (
                candidate.original_device_class == SensorDeviceClass.POWER
            ):
                return candidate.entity_id
        return None

    # --- lifecycle ------------------------------------------------------

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.used_seconds = float(data.get("used_seconds", 0.0))
        self.on_since = dt_util.parse_datetime(data.get("on_since") or "") or None
        # Older stores only had the cursor; treating it as the turn-on moment
        # is the best guess available and only delays the first reading.
        self.running_since = (
            dt_util.parse_datetime(data.get("running_since") or "") or self.on_since
        )
        self.last_tick = dt_util.parse_datetime(data.get("last_tick") or "") or None
        self.last_replaced = dt_util.parse_datetime(data.get("last_replaced") or "") or None
        reference = data.get("reference_power")
        self.reference_power = float(reference) if reference else None
        self.warned = data.get("warned") or None
        stored_history = data.get("history")
        self.history = list(stored_history) if isinstance(stored_history, list) else []
        # Older stores have no counter: the log's length is the best estimate.
        self.refills_closed = int(data.get("refills_closed") or len(self.history))
        stored_basis = data.get("rated_basis")
        self.rated_basis = float(stored_basis) if stored_basis else None

        if "consumed" in data:
            self.consumed = float(data.get("consumed") or 0.0)
            self.has_refill = bool(data.get("has_refill", False))
        else:
            # Storage v1 counted hours against a fixed total and had no concept
            # of an empty socket, so it cannot tell us whether a refill is
            # actually fitted. Assuming "yes" would carry over precisely the
            # comfortable lie this version exists to remove: a trap reading
            # 100 % over an empty bottle. So we ask once, explicitly.
            self.consumed = 0.0
            self.has_refill = False
            _LOGGER.info(
                "%s: upgraded from v1 storage (%.3f h tallied). Refill state is "
                "unknown, so it now reports 'no refill' until you confirm one.",
                self.entry.title,
                self.used_seconds / 3600,
            )
            # A log line was its only trace, and nobody reads the log for
            # something that looks like the trap forgetting itself. A repair
            # issue says it where it is noticed, and clears itself the moment a
            # refill is registered.
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"v1_storage_{self.entry.entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="v1_storage",
                translation_placeholders={
                    "name": self.entry.title,
                    "hours": f"{self.used_seconds / 3600:.1f}",
                },
            )
            self.used_seconds = 0.0

        self._rescale_if_the_bottle_changed()
        self._reconcile_after_restart()

    def _rescale_if_the_bottle_changed(self) -> None:
        """Re-express `consumed` against a bottle whose rated life has changed.

        Consumption is a fraction, which is exactly right for the mode button:
        each slice of time is charged at the rate in force when it was used, so
        flipping to a boosted setting halfway through needs no arithmetic at all.

        Correcting the figure on the box is a different thing. Typing 45 nights
        and fixing it to 30 after 100 hours does not change which hours were
        used, only what fraction of the bottle they were: 100/360 becomes
        100/240. Without this, the percentage simply would not move.

        Keyed on `rated_hours`, never on `lifetime_hours`. The latter carries the
        mode factor, and rescaling on that would rewrite the past every time the
        button moved — undoing the one thing the fraction model exists to get
        right.
        """
        current = self.rated_hours
        if not self.has_refill or not current:
            self.rated_basis = current or None
            return
        previous = self.rated_basis
        if not previous:
            # Nothing to compare against: an older store, or a refill fitted
            # before this existed. Adopt the current bottle without touching
            # what has been counted.
            self.rated_basis = current
            return
        if abs(previous - current) < 0.01:
            return
        before = self.consumed
        self.consumed = round(before * (previous / current), 6)
        self.rated_basis = current
        _LOGGER.info(
            "%s: rated life changed from %.2f h to %.2f h; consumption rescaled "
            "from %.1f %% to %.1f %% (the hours used are unchanged)",
            self.entry.title, previous, current, before * 100, self.consumed * 100,
        )

    def _reconcile_after_restart(self) -> None:
        """Close or continue a period that was open when we stopped."""
        if self.on_since is None:
            return

        state = self.hass.states.get(self.source_entity)
        if state is None or state.state in UNKNOWN_STATES:
            # We are racing the relay's own integration, which may not have
            # loaded yet. Closing the period on that silence would stop
            # counting a trap that is still running — on every restart, until
            # its relay happened to change state by itself. Not knowing means
            # keep counting, the same rule used for Home Assistant's downtime.
            # A relay that turns out to be off is caught on the next tick.
            _LOGGER.debug(
                "%s: relay not readable yet at startup; keeping the open period",
                self.entry.title,
            )
            return

        if state.state not in OFF_STATES:
            # Keep counting from the original timestamp: the refill kept
            # evaporating while Home Assistant was down.
            _LOGGER.debug("%s: still on after restart, keeping the open period", self.entry.title)
            return

        closed_at = self.last_tick or dt_util.utcnow()
        self._accrue(closed_at)
        self.on_since = None
        self.running_since = None

    async def async_start(self) -> None:
        self.power_entity = self._find_power_entity()
        if self.power_entity:
            _LOGGER.debug(
                "%s: watching %s to read the mode back", self.entry.title, self.power_entity
            )

        state = self.hass.states.get(self.source_entity)
        if state is not None and state.state not in OFF_STATES and self.on_since is None:
            self.on_since = dt_util.utcnow()
            self.running_since = self.on_since

        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [self.source_entity], self._handle_state_event
            )
        )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._handle_tick, TICK_INTERVAL)
        )
        await self._async_save()

    async def async_stop(self) -> None:
        """Bank what is owed before we go.

        Reloads happen whenever the entry changes — including a mode change —
        and the rate depends on the mode. Leaving the period open would charge
        the hours already spent at whatever rate applies after the reload.
        """
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self.on_since is not None:
            now = dt_util.utcnow()
            self._accrue(now)
            self.last_tick = now
        await self._async_save()

    # --- accrual ---------------------------------------------------------

    def _accrue(self, until: datetime) -> None:
        """Bank the open period up to `until`, at the rate in force now."""
        if self.on_since is None:
            return
        seconds = max((until - self.on_since).total_seconds(), 0.0)
        self.used_seconds += seconds
        if self.has_refill:
            life = self.lifetime_hours
            if life:
                self.consumed += (seconds / 3600) / life
        self.on_since = until

    # --- events ---------------------------------------------------------

    @callback
    def _handle_state_event(self, event: Event) -> None:
        new = event.data.get("new_state")
        if new is None:
            return
        if new.state in UNKNOWN_STATES:
            # Out of reach is not off. A plug that drops off the network for a
            # few seconds must not chop the period into pieces: the trap is
            # still plugged in and still evaporating. This is the same rule the
            # tick guard and the restart reconciliation follow — all three have
            # to agree, or a flaky relay quietly loses hours and keeps
            # restarting the warm-up the mode detector waits for.
            return

        # Driven by whether a period is open rather than by the previous state,
        # which may be missing entirely when an entity is removed and re-added.
        if new.state not in OFF_STATES:
            if self.on_since is not None:
                return
            self.on_since = dt_util.utcnow()
            self.running_since = self.on_since
            self.last_tick = self.on_since
            self._samples.clear()
            self._warn_if_nothing_to_evaporate()
        else:
            if self.on_since is None:
                return
            self._accrue(dt_util.utcnow())
            self.on_since = None
            self.running_since = None
            self._samples.clear()
        self.entry.async_create_task(self.hass, self._async_save())
        self.async_update_listeners()

    @callback
    def _warn_if_nothing_to_evaporate(self) -> None:
        """Switched on with no refill, or an empty one: say so.

        We deliberately do not switch the relay back off. The integration does
        not own that relay — schedules and automations do — and fighting them
        would produce a loop the user cannot see.
        """
        state = self.refill_state
        if state == REFILL_ACTIVE:
            return
        title = self.entry.title
        key = "no_refill_none" if state == REFILL_NONE else "no_refill_depleted"
        message = t(self.hass, key, name=title) + t(self.hass, "no_refill_tail")
        if self.notify_enabled:
            persistent_notification.async_create(
                self.hass,
                message,
                title=t(self.hass, "no_refill_title"),
                notification_id=f"{DOMAIN}_{self.entry.entry_id}_no_refill",
            )
        self.hass.bus.async_fire(
            EVENT_NO_REFILL,
            {
                "entry_id": self.entry.entry_id,
                "name": title,
                "refill_state": state,
                "switch_entity": self.source_entity,
            },
        )
        _LOGGER.warning("%s: turned on with refill state '%s'", title, state)

    def _definitely_on(self) -> bool:
        """Reported on for certain, as opposed to merely unreachable."""
        state = self.hass.states.get(self.source_entity)
        return state is not None and state.state not in OFF_STATES

    def _definitely_off(self) -> bool:
        """Off for certain, as opposed to merely out of reach.

        An unavailable or missing relay keeps counting on purpose: the trap is
        almost certainly still plugged in and evaporating, the same reasoning
        that counts Home Assistant's own downtime. Only a reported "off" is
        taken as the trap being off.
        """
        state = self.hass.states.get(self.source_entity)
        return state is not None and state.state == "off"

    @callback
    def _handle_tick(self, _now: datetime) -> None:
        if self.on_since is not None:
            # A flaky plug reloads, and Home Assistant removes and re-adds its
            # entities. If it comes back already off, no on->off event ever
            # arrives and we would count phantom hours for ever. Catch that
            # here rather than trusting the event alone.
            if self._definitely_off():
                _LOGGER.debug(
                    "%s: relay reports off with a period still open; closing it",
                    self.entry.title,
                )
                self._accrue(dt_util.utcnow())
                self.on_since = None
                self.running_since = None
                self._samples.clear()
                self.entry.async_create_task(self.hass, self._async_save())
                self.async_update_listeners()
                return
            now = dt_util.utcnow()
            self._accrue(now)
            self.last_tick = now
            self.entry.async_create_task(self.hass, self._async_save())
            self._sample_power(now)
            self._check_level()
        elif self._definitely_on():
            # The mirror of the guard above. A plug that reloads and comes back
            # already on never emits an off->on event, so nothing else would
            # open the period and the hours would vanish in silence — which is
            # the one failure this whole integration exists to prevent.
            now = dt_util.utcnow()
            self.on_since = now
            self.running_since = now
            self.last_tick = now
            self._samples.clear()
            _LOGGER.debug(
                "%s: relay reports on with no period open; opening one",
                self.entry.title,
            )
            self.entry.async_create_task(self.hass, self._async_save())
            self._warn_if_nothing_to_evaporate()
        # Refresh anyway: remaining time and estimates move on their own.
        self.async_update_listeners()

    @callback
    def _check_level(self) -> None:
        """Announce the refill running low, and running out — once each.

        Checked here rather than in the sensors because a sensor is read far
        more often than it changes, and the interesting thing is the crossing,
        not the value. `warned` remembers which side of each line we are on so
        a bottle sitting at 9 % does not announce itself every minute.
        """
        if not self.has_refill:
            return
        pct = self.percent_left
        if pct is None:
            return
        level = (
            "depleted" if self.is_depleted
            else "low" if pct <= self.warn_percent
            else None
        )
        if level == self.warned:
            return

        order = {None: 0, "low": 1, "depleted": 2}
        # .get on a value we did not write this run: a hand-edited store, or one
        # left by a future version with a wider vocabulary, must not raise
        # inside a tick callback.
        improving = order[level] < order.get(self.warned, 0)
        self.warned = level
        self.entry.async_create_task(self.hass, self._async_save())
        if improving:
            # Going back up happens when the duration is corrected, not because
            # anything improved in the socket. Remember it, say nothing.
            return

        key = "depleted" if level == "depleted" else "low"
        event = EVENT_DEPLETED if level == "depleted" else EVENT_LOW
        if self.notify_enabled:
            persistent_notification.async_create(
                self.hass,
                t(
                    self.hass, f"{key}_body",
                    name=self.entry.title, percent=pct,
                    warn=self.warn_percent, hours=self.remaining_hours or 0,
                ),
                title=t(self.hass, f"{key}_title"),
                notification_id=f"{DOMAIN}_{self.entry.entry_id}_{key}",
            )
        self.hass.bus.async_fire(
            event,
            {
                "entry_id": self.entry.entry_id,
                "name": self.entry.title,
                "life_left": pct,
                "remaining_hours": self.remaining_hours,
                "warn_percent": self.warn_percent,
            },
        )
        _LOGGER.info("%s: refill is %s (%.1f %% left)", self.entry.title, level, pct)

    # --- mode detection: the rule ----------------------------------------

    @callback
    def _sample_power(self, now: datetime) -> None:
        """Collect a reading and, once there are enough, judge the mode."""
        if not self.power_entity or self.running_since is None:
            return
        # The warm-up draws well above the steady level; sampling through it
        # would misread the mode every single evening. Measured from when the
        # trap came on — not from `on_since`, which the accrual moves forward
        # every minute and would make this test always true.
        if now - self.running_since < POWER_WARMUP:
            self._samples.clear()
            return
        value = self.power_now
        if value is None or value <= 0:
            return

        self._samples.append((now, value))
        cutoff = now - POWER_WINDOW
        self._samples = [(t, v) for t, v in self._samples if t >= cutoff]
        if len(self._samples) < POWER_MIN_SAMPLES:
            return

        self._judge_mode(median(v for _, v in self._samples))

    @callback
    def _judge_mode(self, reading: float) -> None:
        ratios = self._ratios()
        declared = self._conf.get(CONF_MODE)
        declared_ratio = self._declared_ratio()

        # Nothing learned yet: the reading *is* the reference for whatever mode
        # the user says it is in. Normalised so every other mode follows from it.
        if self.reference_power is None:
            if declared_ratio > 0:
                self.reference_power = round(reading / declared_ratio, 3)
                _LOGGER.info(
                    "%s: reference draw learned, %.2f W in mode '%s'",
                    self.entry.title, reading, declared,
                )
                self.entry.async_create_task(self.hass, self._async_save())
            return

        if len(ratios) < 2 or declared not in ratios:
            return

        expected = {m: self.reference_power * r for m, r in ratios.items()}
        spread = max(expected.values()) - min(expected.values())
        if spread <= 0:
            return

        distances = {m: abs(reading - e) for m, e in expected.items()}
        order = sorted(distances, key=lambda m: distances[m])
        best, runner_up = order[0], order[1]

        if best == declared:
            # Follow the drift only while the reading really sits on top of the
            # declared level. Adapting to anything merely *nearest* would let a
            # slow climb towards another mode drag the reference up with it,
            # and the detector would go blind to the very change it exists for.
            if distances[declared] <= POWER_CLEAR_NEAR * spread:
                self.reference_power = round(
                    (1 - POWER_REFERENCE_ALPHA) * self.reference_power
                    + POWER_REFERENCE_ALPHA * (reading / declared_ratio),
                    3,
                )
                persistent_notification.async_dismiss(
                    self.hass, f"{DOMAIN}_{self.entry.entry_id}_mode_doubt"
                )
                self._doubt_raised = False
                self.entry.async_create_task(self.hass, self._async_save())
            else:
                # Nearest, but not close: halfway between two modes is exactly
                # the case the user asked not to be decided automatically.
                self._raise_mode_doubt(reading, declared, expected)
            return

        unambiguous = (
            distances[best] <= POWER_CLEAR_NEAR * spread
            and distances[runner_up] >= POWER_CLEAR_FAR * spread
        )
        if unambiguous and self.auto_mode:
            self._apply_detected_mode(best, reading, expected)
        elif unambiguous:
            # Confident, but not allowed to act on it. Saying nothing would be
            # worse than the change itself: the count is running at the wrong
            # rate and only the user can decide what to do about it.
            self._raise_mode_doubt(reading, declared, expected)
        elif distances[declared] > POWER_DOUBT * spread:
            self._raise_mode_doubt(reading, declared, expected)

    @callback
    def _apply_detected_mode(
        self, mode: str, reading: float, expected: dict[str, float]
    ) -> None:
        conf = self._conf
        factor = catalog.mode_factor(
            conf.get(CONF_BRAND, ""), conf.get(CONF_MODEL, ""), mode
        )
        previous = conf.get(CONF_MODE)
        _LOGGER.warning(
            "%s: sustained %.2f W matches mode '%s' (%.2f W), not '%s' (%.2f W); switching",
            self.entry.title, reading, mode, expected[mode],
            previous, expected.get(previous, float("nan")),
        )
        if self.notify_enabled:
            persistent_notification.async_create(
                self.hass,
                t(
                    self.hass, "mode_changed_body",
                    name=self.entry.title, reading=reading, mode=mode,
                    expected=expected[mode], previous=previous,
                    was=expected.get(previous, 0),
                ),
                title=t(self.hass, "mode_changed_title"),
                notification_id=f"{DOMAIN}_{self.entry.entry_id}_mode_changed",
            )
        self.hass.bus.async_fire(
            EVENT_MODE_DETECTED,
            {
                "entry_id": self.entry.entry_id,
                "name": self.entry.title,
                "from": previous,
                "to": mode,
                "power": reading,
                "expected": expected,
            },
        )
        self._samples.clear()
        # Updating the options reloads the entry; async_stop() banks the open
        # period first, so the hours already spent keep the old rate.
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={**self.entry.options, CONF_MODE: mode, CONF_MODE_FACTOR: factor},
        )

    @callback
    def _raise_mode_doubt(
        self, reading: float, declared: str | None, expected: dict[str, float]
    ) -> None:
        """In between two levels: report it, do not guess. Once per episode."""
        if self._doubt_raised:
            return
        self._doubt_raised = True
        detail = ", ".join(f"{m} {v:.1f} W" for m, v in sorted(expected.items()))
        if self.notify_enabled:
            persistent_notification.async_create(
                self.hass,
                t(
                    self.hass, "mode_doubt_body",
                    name=self.entry.title, reading=reading, declared=declared,
                    expected=expected.get(declared, 0), detail=detail,
                ),
                title=t(self.hass, "mode_doubt_title"),
                notification_id=f"{DOMAIN}_{self.entry.entry_id}_mode_doubt",
            )
        _LOGGER.warning(
            "%s: sustained %.2f W does not match declared mode '%s'; reporting only",
            self.entry.title, reading, declared,
        )

    # --- actions --------------------------------------------------------

    async def async_replace(self, when: datetime | None = None) -> None:
        """The user fitted a new refill: start the bottle from scratch.

        `when` back-dates it. Fitting a refill on Saturday and remembering to
        say so on Monday is the ordinary case, and recording it as "now" loses
        those days from every estimate that divides by them.

        What back-dating moves is the date, not the tally: we have no record of
        how many hours the trap ran before it was told, so the count still
        starts at zero. That makes the pace read low for the first day or two,
        which is the honest direction to be wrong in — it brings the "runs out"
        date closer rather than pushing it away.
        """
        now = dt_util.utcnow()
        if when is not None:
            # The frontend's datetime selector sends a local wall clock with no
            # offset. Comparing that against an aware `now` raises outright, and
            # storing it poisons every later subtraction that reads it back —
            # days_elapsed, the pace, the estimate and the whole attribute dict.
            if when.tzinfo is None:
                when = when.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            when = dt_util.as_utc(when)
        moment = min(when, now) if when else now
        self._close_current_refill(now)
        self.used_seconds = 0.0
        self.consumed = 0.0
        self.has_refill = True
        self.rated_basis = self.rated_hours
        self.last_replaced = moment
        if self.on_since is not None:
            # A period is open. Charge it from whichever came last: the moment
            # the refill went in, or the moment this run started.
            self.on_since = max(moment, self.running_since or moment)
        self.last_tick = self.on_since
        self._forget_warnings()
        ir.async_delete_issue(self.hass, DOMAIN, f"v1_storage_{self.entry.entry_id}")
        await self._async_save()
        self.async_update_listeners()

    async def async_remove_refill(self) -> None:
        """The socket is empty again.

        "No refill" is a first-class state everywhere in this integration — the
        percentage goes unknown, the warning fires, turning the trap on says so
        — but until now the only way into it was a fresh install. Pressing
        "new refill" once was a one-way door.
        """
        self._close_current_refill(dt_util.utcnow())
        self.used_seconds = 0.0
        self.consumed = 0.0
        self.has_refill = False
        self.rated_basis = None
        self.last_replaced = None
        self._forget_warnings()
        await self._async_save()
        self.async_update_listeners()

    async def async_recalibrate(self) -> None:
        """Forget the learned reference draw and learn it again.

        The reference is learned once, from whatever mode the user said the trap
        was in. Learn it while that was wrong and every later judgement inherits
        the error, with no way back short of deleting the stored file.
        """
        self.reference_power = None
        self._samples.clear()
        persistent_notification.async_dismiss(
            self.hass, f"{DOMAIN}_{self.entry.entry_id}_mode_doubt"
        )
        _LOGGER.info(
            "%s: reference draw cleared; it will be learned again from mode '%s'",
            self.entry.title, self._conf.get(CONF_MODE),
        )
        await self._async_save()
        self.async_update_listeners()

    @callback
    def _close_current_refill(self, closed_at: datetime) -> None:
        """File the refill now in the socket before its counters are wiped.

        Both `async_replace` and `async_remove_refill` start by zeroing the
        tally, which is the only record of how the bottle went. Writing the
        summary first is what turns "the count is gone" into "the last one
        lasted 312 hours over 41 days".
        """
        if not self.has_refill or self.last_replaced is None:
            return
        conf = self._conf
        days = max((closed_at - self.last_replaced).total_seconds() / 86400, 0.0)
        self.history.append(
            {
                "refill": conf.get(CONF_REFILL) or None,
                "fitted": self.last_replaced.isoformat(),
                "closed": closed_at.isoformat(),
                "used_hours": round(self.used_hours, 2),
                "consumed": round(self.consumed_fraction, 3),
                "days": round(days, 1),
            }
        )
        self.refills_closed += 1
        # Oldest first, capped: the store is a log, not an archive.
        del self.history[:-HISTORY_LIMIT]

    @callback
    def _forget_warnings(self) -> None:
        """A different bottle starts with a clean slate."""
        self.warned = None
        for key in ("no_refill", "low", "depleted"):
            persistent_notification.async_dismiss(
                self.hass, f"{DOMAIN}_{self.entry.entry_id}_{key}"
            )

    # --- plumbing -------------------------------------------------------

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "used_seconds": self.used_seconds,
                "consumed": self.consumed,
                "has_refill": self.has_refill,
                "reference_power": self.reference_power,
                "warned": self.warned,
                "history": self.history,
                "refills_closed": self.refills_closed,
                "rated_basis": self.rated_basis,
                "on_since": self.on_since.isoformat() if self.on_since else None,
                "running_since": (
                    self.running_since.isoformat() if self.running_since else None
                ),
                "last_tick": self.last_tick.isoformat() if self.last_tick else None,
                "last_replaced": (
                    self.last_replaced.isoformat() if self.last_replaced else None
                ),
            }
        )

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update)

        def _remove() -> None:
            if update in self._listeners:
                self._listeners.remove(update)

        return _remove

    @callback
    def async_update_listeners(self) -> None:
        for update in list(self._listeners):
            update()
