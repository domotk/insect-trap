"""Built-in catalogue of known traps and their refills.

The catalogue mirrors how the products actually work:

    brand -> model (the appliance)  -> consumable_type   (what it accepts)
                                    -> modes[]           (what its button does)
                                    -> refills[]         (what you can fit)

The appliance fixes the type of refill, so we never ask the user for it. What
varies between refills of the same appliance is only what they are for and how
long they last — which is exactly what the "new refill" form asks.

Modes carry a `factor`: how much faster that setting burns the same bottle.
Bloom states both numbers on the box (45 nights normal, 22 in Max), so the
factor is the ratio between them rather than a round guess.

The data lives in `catalog.json`, not here, because the Lovelace card needs
exactly the same list to fill its dropdowns. One authored file, read by Python
and published to `/local/insect-trap-catalog.json` for the card: two copies of
this list would drift apart within a season.

Anything not listed can still be typed by hand, so an unknown product is never
a dead end.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .const import (
    DEFAULT_HOURS_PER_NIGHT,
    DEFAULT_MODE_FACTOR,
    DEFAULT_TARGET,
    DEFAULT_TYPE,
    UNIT_NIGHTS,
)

_LOGGER = logging.getLogger(__name__)

_CATALOG_FILE = Path(__file__).parent / "catalog.json"

# Used only if catalog.json is missing or corrupt. Deliberately tiny: it exists
# so the integration still loads, not to be a second catalogue.
_FALLBACK: dict[str, Any] = {
    "defaults": {
        "hours_per_night": DEFAULT_HOURS_PER_NIGHT,
        "consumable_type": DEFAULT_TYPE,
        "target": DEFAULT_TARGET,
        "unit": UNIT_NIGHTS,
    },
    "brands": [
        {
            "brand": "Genérico",
            "models": [
                {
                    "model": "Difusor líquido",
                    "consumable_type": "liquid",
                    "modes": [],
                    "refills": [
                        {
                            "name": "Recambio líquido",
                            "target": "mosquitos",
                            "duration": 45.0,
                            "unit": UNIT_NIGHTS,
                        }
                    ],
                }
            ],
        }
    ],
}


def _load() -> dict[str, Any]:
    """Read the catalogue once, at import time.

    Import happens in an executor while the integration is set up, so this
    blocking read never touches the event loop.
    """
    try:
        with _CATALOG_FILE.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not data.get("brands"):
            raise ValueError("catalog.json has no brands")
        return data
    except (OSError, ValueError) as err:
        _LOGGER.error("Could not read %s (%s); using the fallback", _CATALOG_FILE, err)
        return _FALLBACK


DATA: dict[str, Any] = _load()
DEFAULTS: dict[str, Any] = DATA.get("defaults", _FALLBACK["defaults"])


# --- the appliance ------------------------------------------------------


def brands() -> list[str]:
    """Every brand in the catalogue, in the order they were authored."""
    return [b["brand"] for b in DATA.get("brands", [])]


def models(brand: str) -> list[str]:
    """Model names of one brand. Empty when the brand is unknown."""
    for entry in DATA.get("brands", []):
        if entry["brand"] == brand:
            return [m["model"] for m in entry.get("models", [])]
    return []


def all_models() -> list[str]:
    """Every model, across brands, deduplicated and in catalogue order.

    The add form asks for brand and model on the same screen, and Home
    Assistant cannot narrow one dropdown from another field in the same form.
    So the list is the whole catalogue; the field also takes free text.
    """
    out: list[str] = []
    for entry in DATA.get("brands", []):
        for model in entry.get("models", []):
            name = model.get("model")
            if name and name not in out:
                out.append(name)
    return out


def model_info(brand: str, model: str) -> dict[str, Any] | None:
    """The raw catalogue entry for one appliance, or None when typed by hand."""
    for entry in DATA.get("brands", []):
        if entry["brand"] != brand:
            continue
        for candidate in entry.get("models", []):
            if candidate["model"] == model:
                return candidate
    return None


def consumable_type(brand: str, model: str) -> str:
    """What this appliance takes. The user is never asked for a known model."""
    found = model_info(brand, model)
    if found:
        return found.get("consumable_type", DEFAULTS.get("consumable_type", DEFAULT_TYPE))
    return DEFAULTS.get("consumable_type", DEFAULT_TYPE)


def modes(brand: str, model: str) -> list[dict[str, Any]]:
    """The settings this appliance's button offers. Empty when it has none."""
    found = model_info(brand, model)
    return list(found.get("modes", [])) if found else []


def mode_factor(brand: str, model: str, mode_id: str | None) -> float:
    """How much faster the chosen mode burns a refill. 1.0 when unknown."""
    if not mode_id:
        return DEFAULT_MODE_FACTOR
    for mode in modes(brand, model):
        if mode.get("id") == mode_id:
            return float(mode.get("factor", DEFAULT_MODE_FACTOR))
    return DEFAULT_MODE_FACTOR


def mode_power_ratio(brand: str, model: str, mode_id: str | None) -> float:
    """How much more the appliance *draws* in this mode, relative to normal.

    Not the same as `factor`: a Bloom Max in boosted mode empties the bottle
    about twice as fast (2.05) but only draws some 17 % more power. The bottle
    goes faster because the wick runs hotter, not because the heater doubles.

    Measured rather than assumed, which is why it lives in the catalogue next
    to its `power_source` note.
    """
    if not mode_id:
        return 1.0
    for mode in modes(brand, model):
        if mode.get("id") == mode_id:
            return float(mode.get("power_ratio", 1.0))
    return 1.0


def power_ratios(brand: str, model: str) -> dict[str, float]:
    """Every mode's power ratio, for comparing a reading against all of them."""
    return {
        m["id"]: float(m.get("power_ratio", 1.0))
        for m in modes(brand, model)
        if m.get("id")
    }


# --- the refill ---------------------------------------------------------


def refills(brand: str, model: str) -> list[dict[str, Any]]:
    """The refills this appliance accepts."""
    found = model_info(brand, model)
    return list(found.get("refills", [])) if found else []


def refill_names(brand: str, model: str) -> list[str]:
    return [r["name"] for r in refills(brand, model)]


def refill_info(brand: str, model: str, name: str) -> dict[str, Any] | None:
    for refill in refills(brand, model):
        if refill.get("name") == name:
            return refill
    return None


def resolve_refill(brand: str, model: str, name: str | None = None) -> dict[str, Any]:
    """Everything we know about a refill, ready to prefill the form.

    Falls back to sensible defaults for anything typed by hand, so the caller
    never has to care whether the product was known.
    """
    out: dict[str, Any] = {
        "refill": name or "",
        "target": DEFAULTS.get("target", DEFAULT_TARGET),
        "duration": 45.0,
        "unit": DEFAULTS.get("unit", UNIT_NIGHTS),
        "note": "",
        "verified": False,
    }
    available = refills(brand, model)
    found = refill_info(brand, model, name) if name else (available[0] if available else None)
    if not found:
        return out

    out["refill"] = found.get("name", out["refill"])
    out["target"] = found.get("target", out["target"])
    out["duration"] = float(found.get("duration", out["duration"]))
    out["unit"] = found.get("unit", out["unit"])
    out["note"] = found.get("note", "")
    out["verified"] = bool(found.get("verified", False))
    return out
