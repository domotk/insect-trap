"""Free text the integration writes at runtime, in the languages it ships.

Home Assistant's translation files cover the interface — entity names, flow
labels, selector options — but not text a component composes while running,
which is exactly what a persistent notification is. Those were hard-coded in
Spanish inside an integration whose interface is bilingual, so an English user
got English menus and Spanish alarms.

Picked by `hass.config.language`, with English as the fallback for anything
else. Two languages is not a translation framework, and it does not need to be:
the alternative was one.
"""

from __future__ import annotations

from typing import Any

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "no_refill_title": "Trap with no refill",
        "no_refill_none": "**{name}** has been switched on with no refill fitted.",
        "no_refill_depleted": "**{name}** has been switched on with its refill spent.",
        "no_refill_tail": (
            " It is not protecting anything. Register a new refill once you fit one."
        ),
        "mode_changed_title": "Mode corrected from the power draw",
        "mode_changed_body": (
            "**{name}** has been drawing {reading:.1f} W steadily for twenty minutes, "
            "which matches mode **{mode}** ({expected:.1f} W) rather than **{previous}** "
            "({was:.1f} W).\n\nThe mode has been changed and the refill's life "
            "recalculated. If that is wrong, set it back from the «Mode» control."
        ),
        "mode_doubt_title": "Power draw does not match the mode",
        "mode_doubt_body": (
            "**{name}** is steadily drawing {reading:.1f} W, which does not match "
            "the declared mode (**{declared}**, {expected:.1f} W).\n\n"
            "References: {detail}.\n\nNothing was changed, because the reading "
            "sits half way between two levels. Check the appliance itself."
        ),
        "low_title": "Refill running low",
        "low_body": (
            "**{name}** has {percent:.0f} % of its refill left, below the {warn:.0f} % "
            "you asked to be warned at.\n\nAbout {hours:.0f} running hours to go."
        ),
        "depleted_title": "Refill spent",
        "depleted_body": (
            "**{name}** has used up its refill. From here it evaporates nothing, "
            "however long it stays on.\n\nFit a new one and register it."
        ),
    },
    "es": {
        "no_refill_title": "Trampa sin recarga",
        "no_refill_none": "**{name}** se ha encendido y no tiene recarga puesta.",
        "no_refill_depleted": "**{name}** se ha encendido con la recarga agotada.",
        "no_refill_tail": (
            " No está protegiendo nada. Marca una recarga nueva cuando la pongas."
        ),
        "mode_changed_title": "Modo corregido por consumo",
        "mode_changed_body": (
            "**{name}** lleva veinte minutos consumiendo {reading:.1f} W de forma "
            "sostenida, que corresponde al modo **{mode}** ({expected:.1f} W) y no a "
            "**{previous}** ({was:.1f} W).\n\nHe cambiado el modo y recalculado "
            "la vida útil de la recarga. Si no es correcto, cámbialo desde el "
            "control «Modo»."
        ),
        "mode_doubt_title": "Consumo que no cuadra con el modo",
        "mode_doubt_body": (
            "**{name}** consume {reading:.1f} W de forma sostenida, que no encaja "
            "con el modo declarado (**{declared}**, {expected:.1f} W).\n\n"
            "Referencias: {detail}.\n\nNo lo he cambiado porque la lectura queda "
            "a medio camino. Compruébalo en el aparato."
        ),
        "low_title": "Recarga a punto de agotarse",
        "low_body": (
            "**{name}** tiene el {percent:.0f} % de recarga, por debajo del "
            "{warn:.0f} % que pediste que te avisara.\n\nLe quedan unas "
            "{hours:.0f} horas de funcionamiento."
        ),
        "depleted_title": "Recarga agotada",
        "depleted_body": (
            "**{name}** ha gastado su recarga. A partir de aquí no evapora nada, "
            "por muchas horas que siga encendida.\n\nPon una nueva y márcala."
        ),
    },
}


def t(hass: Any, key: str, **variables: Any) -> str:
    """One notification string, in the user's language."""
    lang = str(getattr(hass.config, "language", "") or "en").split("-")[0]
    table = TEXTS.get(lang) or TEXTS["en"]
    text = table.get(key) or TEXTS["en"].get(key) or key
    return text.format(**variables) if variables else text
