# Insect Traps

Tracks the refill of a plug-in insect trap by **the hours it actually runs**,
not by days on a calendar.

A liquid refill is rated in *nights* — 45 nights, 30 nights, 90 for a pack of
two. What the box means by a night is 8 hours. If your trap runs from 19:00 to
07:00 it is burning a night and a half every night, and a refill rated for 45
will be gone in 30. Counting days cannot see that. Counting the hours the relay
is actually on can.

## Why an integration and not a template sensor

The recorder keeps ten days of history by default. A refill lasts a month or
more, so counting from history silently loses hours. This keeps its own tally in
`.storage` and never depends on the recorder.

Two consequences worth knowing about:

- **Consumption is stored as a fraction of the bottle, not as hours.** The mode
  button is physical and can be flipped at any time, and a boosted setting empties
  the same bottle faster. Counting hours against a fixed total would claim 76
  hours left after 100 of 360, when the truth is that 28 % of the bottle is gone
  and the remaining 72 % lasts about 127 hours at the new rate.
- **A trap with no refill reports "no refill", never 100 %.** A comfortable lie
  there is one you cannot see through until the mosquitoes tell you.

## Reading the mode from the power draw

The boosted setting is a physical button and nothing tells Home Assistant it
moved. The cost of not noticing is real: a trap can run for days in Max while the
count assumes Normal.

Each mode draws a different and very steady amount, so a sustained reading gives
it away. Two cautions are built in: the warm-up right after switching on reads
high and is ignored, and a reading that falls between two levels is reported
rather than forced into a decision.

If the guess is wrong, point it at a different meter, relearn the reference with
the `recalibrate` service, or switch the automatic change off and keep only the
warning.

## Entities

Each trap becomes one device, linked to the relay that powers it.

| Entity | What it is |
| --- | --- |
| `sensor.<trap>_life_left` | Percentage left. The primary sensor: everything else is in its attributes |
| `sensor.<trap>_hours_used` | Running hours on this refill |
| `sensor.<trap>_hours_left` | Hours left, in the mode it is set to |
| `sensor.<trap>_nights_left` | Nights left at your real pace, not the box's 8 h |
| `sensor.<trap>_runs_out` | Estimated date |
| `sensor.<trap>_last_replaced` · `sensor.<trap>_refills_logged` | Diagnostic |
| `binary_sensor.<trap>_replacement_due` | On when there is no refill, it is spent, or it is low |
| `button.<trap>_new_refill_fitted` · `button.<trap>_refill_taken_out` | The two halves of the pair |
| `select.<trap>_mode` | Only for appliances whose button offers more than one setting |

`nights_left`, `runs_out` and `avg_hours_per_day` stay empty until there is half
a day of history behind them. That is deliberate: an average over ten minutes is
not an average.

## Services

| Service | What it does |
| --- | --- |
| `insect_trap.replace_refill` | Registers a new refill. `reset: false` corrects a detail without losing the count; `fitted_at` back-dates it |
| `insect_trap.remove_refill` | The socket is empty again |
| `insect_trap.recalibrate` | Forgets the learned reference draw and learns it again |

`replace_refill` takes **only refill data** — which refill, what it is for, how
long it lasts. Brand, model and mode describe the appliance and are changed in
the integration's options.

## Events

`insect_trap_low`, `insect_trap_depleted`, `insect_trap_no_refill` and
`insect_trap_mode_detected`. They fire whether or not persistent notifications
are switched on, so automations keep working either way.

## Setting it up

Add the integration, give the trap a name and pick the switch that powers it,
then its brand, model and refill type. A new trap starts with **no refill**, so
the first thing it asks you to do is register one.

The refill type and the available modes come from a small built-in catalogue
(`catalog.json`) with figures taken from manufacturers and retailers. Anything
not listed can be typed by hand, and the catalogue marks any figure that could
not be confirmed.

## Installation

HACS → three-dot menu → *Custom repositories* → this repository, category
*Integration*. Then install and restart Home Assistant.

## Diagnostics

The device page offers a download with the tally, the mode detector's state and
the refill history — everything needed to work out why a trap is miscounting,
without asking anyone to read attributes off a screen.

## The card

The Lovelace card that goes with this lives in its own repository, because HACS
treats a repository as one category and this one is an integration. The
integration is perfectly usable without it.

## Built with

Written with [Claude Code](https://claude.com/claude-code). Every push is checked
by [hassfest](https://developers.home-assistant.io/blog/2020/04/16/hassfest/),
Home Assistant's own integration validator, and by the
[HACS action](https://github.com/hacs/action).

## Licence

MIT. The catalogue figures are referenced to their sources inside
`catalog.json`; product names belong to their respective owners.
