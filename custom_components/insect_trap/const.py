"""Constants for the Insect Traps integration.

Two things that are easy to confuse, kept deliberately apart:

  * the TRAP is the appliance. It fixes the kind of refill it accepts (liquid,
    tablet…) and the modes its button offers. It is set up once.
  * the REFILL is what you fit into it. Only two things vary between refills of
    the same appliance: what they are for, and how long they last.

Everything downstream follows from that split: the add flow asks about the
trap, the "new refill" form asks about the refill, and neither asks the other's
questions.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "insect_trap"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
]

# --- the trap (set up once) --------------------------------------------
CONF_SWITCH = "switch_entity"
CONF_BRAND = "brand"
CONF_MODEL = "model"
CONF_TYPE = "consumable_type"     # liquid | tablet | other — fixed by the model
CONF_MODE = "mode"                # the appliance's button, changeable any time
CONF_MODE_FACTOR = "mode_factor"  # how much faster this mode burns the refill
CONF_HOURS_PER_NIGHT = "hours_per_night"
CONF_WARN_PERCENT = "warn_percent"
CONF_NOTIFY = "notify"            # persistent notifications; events fire regardless
# The mode detector, and the two things the user had no say over. The meter was
# found automatically with no way to correct it, and a detected mode rewrote the
# configuration whether or not the user wanted it touched.
CONF_POWER_ENTITY = "power_entity"   # empty means "find it yourself"
CONF_AUTO_MODE = "auto_mode"         # off: report the mismatch, change nothing

# --- the refill (changes every time you fit one) ------------------------
CONF_REFILL = "refill"            # the label shown for it, free text or catalogue
CONF_TARGET = "target"            # what this refill is for
CONF_DURATION = "duration"        # the number printed on the box
CONF_DURATION_UNIT = "duration_unit"
# Derived from the three above, never stored: a stored copy was preferred over
# the calculation and frozen when a refill was registered, which is what made
# "hours per night" a setting that changed nothing. The key survives only so the
# v2 -> v3 migration can find and remove what older entries still carry.
CONF_LIFETIME_HOURS = "lifetime_hours"

# --- service-only fields (never stored on the entry) ---------------------
CONF_RESET = "reset"              # correct the details without losing the count
CONF_FITTED_AT = "fitted_at"      # "I put it in on Saturday and forgot to say"

# --- units --------------------------------------------------------------
# A box states either nights ("45 noches") or plain hours ("10 días x 24 h",
# which is 240 hours of running, not 240 nights). Those are the only two ways
# the industry expresses it, so those are the only two we offer.
UNIT_NIGHTS = "nights"
UNIT_HOURS = "hours"
UNITS = [UNIT_NIGHTS, UNIT_HOURS]

# --- refill state -------------------------------------------------------
# A trap with no refill must never read 100 %: that is a lie the user cannot
# see through. It reads "none" until one is actually fitted.
REFILL_NONE = "none"
REFILL_ACTIVE = "active"
REFILL_DEPLETED = "depleted"

# --- defaults -----------------------------------------------------------
# The industry rates refills in "nights" of 8 hours (Raid, Relec, Bloom).
DEFAULT_HOURS_PER_NIGHT = 8.0
DEFAULT_DURATION = 45.0
DEFAULT_UNIT = UNIT_NIGHTS
DEFAULT_WARN_PERCENT = 10.0
DEFAULT_NOTIFY = True
DEFAULT_AUTO_MODE = True
DEFAULT_TYPE = "liquid"
DEFAULT_TARGET = "mosquitos"
DEFAULT_MODE_FACTOR = 1.0

TYPES = ["liquid", "tablet", "other"]
# A refill is sold for mosquitoes, for mosquitoes and flies, or for a wider mix.
TARGETS = ["mosquitos", "mosquitos_moscas", "varios"]

# --- events and notifications -------------------------------------------
EVENT_NO_REFILL = "insect_trap_no_refill"
EVENT_MODE_DETECTED = "insect_trap_mode_detected"
# The two most ordinary things that can happen to a refill had no event at all:
# the binary sensor moved and that was it, so anyone wanting to be told had to
# build the automation themselves.
EVENT_LOW = "insect_trap_low"
EVENT_DEPLETED = "insect_trap_depleted"

# --- detecting the mode from the power draw ------------------------------
# The appliance's button is physical: nothing tells Home Assistant it moved.
# But each mode draws a different, very steady amount of power, so a sustained
# reading gives it away — as long as we are careful about two things.
#
# First, the warm-up: every trap draws noticeably more for the first stretch
# after switching on (8.2 W against a steady 7.5 W on the units measured), so
# sampling has to wait it out or it would misread every single evening.
#
# Second, the doubt: the two levels are only about 1 W apart, so a reading in
# between must not be forced into a decision. It only switches the mode when
# the reading sits right on top of one level and far from the others.
POWER_WARMUP = timedelta(minutes=20)
POWER_WINDOW = timedelta(minutes=30)
POWER_MIN_SAMPLES = 20

# As fractions of the distance between two modes' expected draws, so the rule
# scales to any product instead of hard-coding watts.
POWER_CLEAR_NEAR = 0.25     # must be this close to the winning level
POWER_CLEAR_FAR = 0.75      # and at least this far from the next one
POWER_DOUBT = 0.5           # beyond this from the declared mode, say something

# The reference adapts slowly, so a one-off draught or a dying bottle does not
# drag it, but a genuine drift still gets followed.
POWER_REFERENCE_ALPHA = 0.1

# --- storage ------------------------------------------------------------
# Deliberately still 1. The stored shape did change — consumption is now a
# fraction of the bottle — but Home Assistant's Store answers a bumped version
# by calling a migration function that only exists if you subclass it, and
# raises NotImplementedError otherwise, which stops every entry from loading.
# The format is detected in async_load() by looking for the "consumed" key,
# which is explicit, testable, and needs no version number at all.
# The config entry schema version. Kept here so the flow and the migration
# guard cannot drift apart: one of them lagging silently refuses to load every
# entry, and says so only in the log.
CONFIG_VERSION = 4

STORAGE_VERSION = 1

# How many finished refills to keep. Enough to answer "how long did the last few
# last", short enough that the store stays a few kilobytes: this is a log, not a
# database, and the recorder already holds the detail.
HISTORY_LIMIT = 20

# While the device is on we persist a heartbeat, so a crash or a power cut
# costs at most this much accounting, and we can still close the period.
TICK_INTERVAL = timedelta(minutes=1)

# Attribute names shared with the cards.
ATTR_BRAND = "brand"
ATTR_MODEL = "model"
ATTR_MODE = "mode"
ATTR_TYPE = "consumable_type"
ATTR_TARGET = "target"
ATTR_REFILL = "refill"
ATTR_REFILL_STATE = "refill_state"
ATTR_LIFETIME_HOURS = "lifetime_hours"
ATTR_USED_HOURS = "used_hours"
ATTR_REMAINING_HOURS = "remaining_hours"
ATTR_LAST_REPLACED = "last_replaced"
ATTR_AVG_HOURS_PER_DAY = "avg_hours_per_day"
ATTR_POWER_ENTITY = "power_entity"
ATTR_POWER_NOW = "power_now"
ATTR_POWER_REFERENCE = "power_reference"
ATTR_POWER_EXPECTED = "power_expected"


def lifetime_hours(duration: float, unit: str, hours_per_night: float) -> float:
    """Turn what the box says into hours, at the appliance's normal setting.

    Kept here, next to the units it uses, because the config flow, the options
    flow and the replace service all have to agree on it. Two copies of this
    arithmetic would eventually disagree and silently miscount a refill.
    """
    value = float(duration or 0)
    if unit == UNIT_HOURS:
        return round(value, 2)
    return round(value * float(hours_per_night or DEFAULT_HOURS_PER_NIGHT), 2)


def effective_lifetime_hours(raw_hours: float, factor: float) -> float:
    """How long the refill really lasts in the mode the trap is set to.

    The rated life on the box assumes the normal setting. A boosted mode
    evaporates the same bottle faster, so it divides that life.
    """
    divisor = float(factor or DEFAULT_MODE_FACTOR)
    if divisor <= 0:
        divisor = DEFAULT_MODE_FACTOR
    return round(float(raw_hours or 0) / divisor, 2)
