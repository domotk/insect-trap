"""Config and options flow.

The add flow describes the APPLIANCE: name, relay, brand, model and the kind of
refill it takes. It deliberately asks nothing about a refill, so a new trap
starts with an empty socket and says so. Registering a refill is a separate act
with its own form.

It is split into three screens on purpose. Home Assistant builds a form's schema
once, when the step is rendered, so no field can react to another field of the
same form without submitting it. Submitting the brand is therefore what lets the
next screen offer only that brand's models — and submitting the model is what
lets the last screen prefill the refill type and the modes that model actually
has. Every product field also accepts free text, so an unlisted trap is never a
dead end.

The options flow has no such dependency to resolve, so it stays a single form
with those same fields plus the warning threshold.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from . import catalog
from .const import (
    CONFIG_VERSION,
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
    CONF_TARGET,
    CONF_TYPE,
    CONF_WARN_PERCENT,
    DEFAULT_AUTO_MODE,
    DEFAULT_HOURS_PER_NIGHT,
    DEFAULT_NOTIFY,
    DEFAULT_TYPE,
    DEFAULT_WARN_PERCENT,
    DOMAIN,
    TARGETS,
    TYPES,
    UNITS,
)


def _select(options: list[str], key: str | None = None, custom: bool = False):
    """A dropdown; `custom` lets the user type something we do not know.

    Optional keys are omitted rather than passed as None: the selector schemas
    type them as strings, so an explicit None is rejected and the whole form
    fails to build — which surfaces as a bare HTTP 400 with nothing in the log.
    """
    config: dict[str, Any] = {
        "options": options,
        "custom_value": custom,
        "mode": selector.SelectSelectorMode.DROPDOWN,
    }
    if key:
        config["translation_key"] = key
    return selector.SelectSelector(selector.SelectSelectorConfig(**config))


def _number(minimum: float, maximum: float, step: float, unit: str | None = None):
    """A number box. Same rule: no key at all beats a key set to None."""
    config: dict[str, Any] = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": selector.NumberSelectorMode.BOX,
    }
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(selector.NumberSelectorConfig(**config))


def _switch_selector():
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["switch", "light", "fan"])
    )


def _power_selector():
    """The power meter, as plain text. Empty means "find it yourself".

    Deliberately not an EntitySelector. This field has to be clearable — that is
    how the user goes back to automatic discovery — and an entity selector
    validates whatever it is handed, so the empty string it is left with fails
    the schema. That failure surfaces as a bare HTTP 400 with nothing in the
    log, which is the same trap `_select` and `_number` above are shaped to
    avoid. A text box costs a prettier widget and buys a form that cannot break.
    """
    return selector.TextSelector()


def _warn_selector():
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=50,
            step=1,
            mode=selector.NumberSelectorMode.SLIDER,
            unit_of_measurement="%",
        )
    )


class InsectTrapConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one trap. Refills are registered afterwards, not here."""

    VERSION = CONFIG_VERSION

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_model()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): selector.TextSelector(),
                vol.Required(CONF_SWITCH): _switch_selector(),
                vol.Required(CONF_BRAND): _select(catalog.brands(), custom=True),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Only the models of the brand just chosen."""
        brand = self._data.get(CONF_BRAND, "")

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_detalles()

        schema = vol.Schema(
            {vol.Required(CONF_MODEL): _select(catalog.models(brand), custom=True)}
        )
        return self.async_show_form(
            step_id="model",
            data_schema=schema,
            description_placeholders={"brand": brand},
        )

    async def async_step_detalles(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Refill type and, when the appliance has one, the mode.

        Both are prefilled from the catalogue now that the model is known, so
        for a listed product this screen is usually just confirming.
        """
        brand = self._data.get(CONF_BRAND, "")
        model = self._data.get(CONF_MODEL, "")
        available = catalog.modes(brand, model)

        if user_input is not None:
            self._data.update(user_input)
            # What the catalogue says this appliance's refill lasts. Leaving it
            # out is what made a brand-new trap useless: with no duration its
            # rated life came out as zero, so the percentage stayed unknown, not
            # one hour was ever tallied and no warning could fire — silently,
            # on every fresh install.
            #
            # This is not the add flow asking about a refill; it still does not.
            # These are the figures to correct, not a question to answer.
            sugerida = catalog.resolve_refill(brand, model)
            data = {
                **self._data,
                CONF_MODE_FACTOR: catalog.mode_factor(
                    brand, model, self._data.get(CONF_MODE)
                ),
                CONF_HOURS_PER_NIGHT: DEFAULT_HOURS_PER_NIGHT,
                CONF_WARN_PERCENT: DEFAULT_WARN_PERCENT,
                CONF_TARGET: sugerida["target"],
                CONF_DURATION: sugerida["duration"],
                CONF_DURATION_UNIT: sugerida["unit"],
            }
            await self.async_set_unique_id(
                f"{DOMAIN}_{data[CONF_SWITCH]}", raise_on_progress=False
            )
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=data[CONF_NAME], data=data)

        fields: dict[Any, Any] = {
            vol.Required(
                CONF_TYPE, default=catalog.consumable_type(brand, model)
            ): _select(TYPES, key="consumable_type")
        }
        if available:
            fields[vol.Required(CONF_MODE, default=available[0]["id"])] = _select(
                [m["id"] for m in available], key="mode"
            )

        return self.async_show_form(
            step_id="detalles",
            data_schema=vol.Schema(fields),
            description_placeholders={"product": f"{brand} · {model}".strip(" ·")},
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return InsectTrapOptionsFlow()


class InsectTrapOptionsFlow(OptionsFlow):
    """Everything the add flow asked, plus the warning, in one screen.

    Grouped into three labelled sections, because a flat list mixed "which plug
    is it on" with "how long does its refill last" and they are not the same
    kind of question.

    Here the model dropdown lists the whole catalogue: with a single screen
    there is no submitted brand to filter by, and hiding the other brands would
    make switching appliance impossible.
    """

    def _trap_using(self, switch: str | None) -> ConfigEntry | None:
        """Another trap already counting that relay, if there is one.

        Compares the switch each entry actually has, not its unique id. The id
        is minted when the trap is added and never touched again, so after a
        switch is changed here it no longer describes reality — which is the
        very reason this collision could happen at all.
        """
        if not switch:
            return None
        for otra in self.hass.config_entries.async_entries(DOMAIN):
            if otra.entry_id == self.config_entry.entry_id:
                continue
            if {**otra.data, **otra.options}.get(CONF_SWITCH) == switch:
                return otra
        return None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        errores: dict[str, str] = {}
        ocupada = ""
        brand = current.get(CONF_BRAND, "")
        model = current.get(CONF_MODEL, "")
        available = catalog.modes(brand, model)

        if user_input is not None:
            # Sections arrive nested, one dict per section. Flatten them or the
            # entry would be written with the section names as its keys.
            flat: dict[str, Any] = {}
            for key, value in user_input.items():
                if isinstance(value, dict):
                    flat.update(value)
                else:
                    flat[key] = value

            merged = {**current, **flat}

            # Two traps on one relay would both count its hours and both report
            # figures that are false. The add flow refuses it through the unique
            # id; changing the switch afterwards walked straight past that.
            choque = self._trap_using(flat.get(CONF_SWITCH))
            if choque is not None:
                errores["base"] = "switch_in_use"
                ocupada = choque.title
            else:
                nuevo_switch = flat.get(CONF_SWITCH)
                if nuevo_switch and nuevo_switch != current.get(CONF_SWITCH):
                    # Keep the unique id honest. Left stale, adding a trap on
                    # the relay this one has just vacated would be refused for
                    # ever, with nothing to explain why.
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, unique_id=f"{DOMAIN}_{nuevo_switch}"
                    )
                return self.async_create_entry(
                    data={
                        **self.config_entry.options,
                        **flat,
                        CONF_MODE_FACTOR: catalog.mode_factor(
                            merged.get(CONF_BRAND, ""),
                            merged.get(CONF_MODEL, ""),
                            merged.get(CONF_MODE),
                        ),
                    }
                )

        aparato: dict[Any, Any] = {
            vol.Required(CONF_BRAND, default=brand): _select(
                catalog.brands(), custom=True
            ),
            vol.Required(CONF_MODEL, default=model): _select(
                catalog.all_models(), custom=True
            ),
            vol.Required(
                CONF_TYPE, default=current.get(CONF_TYPE, DEFAULT_TYPE)
            ): _select(TYPES, key="consumable_type"),
        }
        if available:
            aparato[
                vol.Required(
                    CONF_MODE, default=current.get(CONF_MODE) or available[0]["id"]
                )
            ] = _select([m["id"] for m in available], key="mode")

        # What the catalogue knows about this appliance's refills, used only to
        # fill the dropdown and the defaults. A flow cannot react to one of its
        # own fields without submitting it, so picking a listed refill does not
        # rewrite the duration beside it: the number stays the user's to set,
        # and the catalogue's figure is merely what it starts at.
        conocidas = catalog.refill_names(brand, model)
        sugerida = catalog.resolve_refill(brand, model, current.get(CONF_REFILL))

        recarga: dict[Any, Any] = {
            vol.Optional(
                CONF_REFILL, default=current.get(CONF_REFILL) or ""
            ): _select(conocidas, custom=True),
            vol.Required(
                CONF_TARGET, default=current.get(CONF_TARGET) or sugerida["target"]
            ): _select(TARGETS, key="target"),
            vol.Required(
                CONF_DURATION,
                default=float(current.get(CONF_DURATION) or sugerida["duration"]),
            ): _number(1, 10000, 1),
            vol.Required(
                CONF_DURATION_UNIT,
                default=current.get(CONF_DURATION_UNIT) or sugerida["unit"],
            ): _select(UNITS, key="duration_unit"),
        }

        deteccion: dict[Any, Any] = {
            vol.Optional(
                CONF_POWER_ENTITY, default=current.get(CONF_POWER_ENTITY) or ""
            ): _power_selector(),
            vol.Required(
                CONF_AUTO_MODE,
                default=current.get(CONF_AUTO_MODE, DEFAULT_AUTO_MODE),
            ): selector.BooleanSelector(),
        }

        schema = vol.Schema(
            {
                vol.Required("conexion"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_SWITCH, default=current.get(CONF_SWITCH)
                            ): _switch_selector()
                        }
                    ),
                    {"collapsed": False},
                ),
                vol.Required("aparato"): section(
                    vol.Schema(aparato), {"collapsed": False}
                ),
                # Editing these does NOT restart the count. Registering a new
                # refill is a separate act with its own service and its own
                # button: correcting a duration typed wrong must not cost the
                # user the hours already tallied.
                vol.Required("recarga"): section(
                    vol.Schema(recarga), {"collapsed": True}
                ),
                # Only worth showing for an appliance that has modes to tell
                # apart: with a single setting there is nothing to detect and
                # the power meter is never read.
                **(
                    {
                        vol.Required("deteccion"): section(
                            vol.Schema(deteccion), {"collapsed": True}
                        )
                    }
                    if available
                    else {}
                ),
                vol.Required("aviso"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_HOURS_PER_NIGHT,
                                default=current.get(
                                    CONF_HOURS_PER_NIGHT, DEFAULT_HOURS_PER_NIGHT
                                ),
                            ): _number(1, 24, 0.5, "h"),
                            vol.Required(
                                CONF_WARN_PERCENT,
                                default=current.get(
                                    CONF_WARN_PERCENT, DEFAULT_WARN_PERCENT
                                ),
                            ): _warn_selector(),
                            vol.Required(
                                CONF_NOTIFY,
                                default=current.get(CONF_NOTIFY, DEFAULT_NOTIFY),
                            ): selector.BooleanSelector(),
                        }
                    ),
                    {"collapsed": False},
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errores,
            description_placeholders={"other": ocupada},
        )
