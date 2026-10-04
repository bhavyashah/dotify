"""No silent chords: the acknowledgment-flash copy contract.

Every refusal / no-op answer a chord can flash must

* FIT the smallest supported display — 20 cells, of which 18 are content
  cells once the gauge takes its pair — in grade-1 (uncontracted)
  braille, the widest the text can render (digits cost a number sign);
* stay DISTINCT from every other answer in the family, so no two
  different refusals can ever feel identical under the fingers.

The list is maintained BY HAND next to the audit table in
docs/silent-chord-audit.md: a new refusal flash joins both. Behavior —
that each of these actually fires on its silent path — is pinned where
the handler lives (test_controls, test_display_keys, test_panning,
test_advance_modes); this module owns only the copy contract.
"""

from braille_engine.engine import BrailleEngine
from braille_engine.gauge import BacklogGauge
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator

# One entry per distinct meaning. The paused refusal is deliberately ONE
# message shared by pan back / pan forward / the manual advance press —
# one cause, one answer. Confirmations of real state changes ("grade 2",
# "resumed", "auto"...) are not listed: the family here is the answers
# that explain why nothing visibly happened, plus the relay chord's
# immediate acknowledgment (the only answer a shell-less run gives).
ACK_FLASHES = (
    "already live",         # jump-to-live with nothing pending
    "nothing to recap",     # summarize with nothing missed
    "no summary",           # summarize: no summarizer / fetch failed
    "at start",             # pan back at history's start
    "live",                 # pan forward with nothing renderable
    "display paused",       # pan / manual advance under a reader pause
    "caught up",            # manual advance with nothing to flip
    "text on its way",      # manual advance on a still-soft head
    "viewing history",      # terminal advance request while panned
    "manual, no pace",      # slower in manual mode
    "window: auto only",    # window cycle outside auto
    "no grade 1",           # grade set refused (needs liblouis)
    "no grade 2",
    "no grade 3",
    "input pause asked",    # Space+dot-6 relay: immediate ack
)

CONTENT_CELLS = 18          # the 20-cell eReader minus the gauge pair


def _engine():
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=20, echo=False),
                        gauge=BacklogGauge())
    eng.start()
    return eng


def test_every_ack_fits_the_smallest_display_in_grade_1():
    eng = _engine()
    assert eng.content_width == CONTENT_CELLS
    for text in ACK_FLASHES:
        _cells, total = eng.fit_cells(text, CONTENT_CELLS)
        assert total <= CONTENT_CELLS, (
            f"{text!r} is {total} grade-1 cells — it would truncate on "
            f"the 20-cell eReader ({CONTENT_CELLS} content cells)")


def test_every_ack_is_distinct():
    assert len(set(ACK_FLASHES)) == len(ACK_FLASHES)
