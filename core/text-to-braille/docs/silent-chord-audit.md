# Chord acknowledgement audit

Rule: **every braille chord produces a tactile acknowledgement**. Either the
state under the fingers visibly changes, or an announcement flash says why
it didn't. This is the enumeration of the paths where a chord would
otherwise change nothing, with the flash each one gives. All of them use
the ordinary announcement mechanism (dismissable, pace-scaled dwell, shown
even while paused). The flash texts fit the 18 content cells of a 20-cell
display in grade 1, are all distinct, and are pinned in
`tests/test_chord_acks.py`.

## Paths that answer with a flash

| Chord / key                        | Why nothing changed                                       | Flash            |
|------------------------------------|-----------------------------------------------------------|------------------|
| Thumb Next (jump to live)          | already at the live edge with nothing pending             | `already live`   |
| Space+S (summarize)                | nothing missed and the view already live                  | `nothing to recap` |
| Space+S (summarize)                | no summarizer configured, or the fetch failed (snaps)     | `no summary`     |
| Space+dot-6 (input pause relay)    | only a request to the shell; nothing answers on a shell-less run | `input pause asked` (a shell's own `mic off` / `mic on` replaces it) |
| Space+G (grade)                    | liblouis missing, grade refused                           | `no grade 2` / `no grade 3` |
| Thumb Left / Right (pan)           | refused while the display is paused                       | `display paused` |
| Thumb Left (pan back)              | at the start of history                                   | `at start`       |
| Thumb Right (pan forward)          | already live with nothing renderable queued               | `live`           |
| Space+dot-4 / f / Thumb Right (manual advance) | caught up                                    | `caught up`      |
| — same press                       | the next segment is not final yet                         | `text on its way` |
| — same press                       | display paused                                            | `display paused` |
| — same press (terminal f)          | viewing panned history                                    | `viewing history` |
| Space+dot-1 / s in manual mode     | manual mode has no pace to slow                           | `manual, no pace` |
| Space+W outside auto mode          | manual mode always flips full pages                       | `window: auto only` |
| any non-typing chord in reply mode | refused while composing                                   | `space r to exit` |

Every setting change also confirms itself (grade, window, advance mode,
pace, status, demo on/off, reply start/end, resume).

## Deliberately silent

* **Pausing** (Space+dot-3 / p). The freeze itself is the confirmation, and
  a flash over the very cells the reader chose to hold would defeat the
  pause. Resuming flashes `resumed`. The panel's `set_paused` is
  idempotent, so a stale button press can't double-toggle or re-flash.
* **Unmapped combos** (Space+dot-2/dot-5, routing keys, bare dots while
  reading). They are not commands and only reach the discovery log;
  flashing them would punish resting fingers, and they don't even dismiss
  an active flash.
* **Manual advance during a summary fetch.** Flashes are refused while the
  fetch holds the display, and the press is already answered by the hold's
  own frame (drained gauge, `s` in cell 2).
* **Human-mode typed keys.** Content, not commands.
* **Panel relay setters** re-asking the current state (`set_paused`,
  `set_grade`, `set_advance_mode`). Idempotent by contract; the panel is a
  sighted UI with its own feedback.
