# poolside_auto_compress

Auto-runs `/compact` in the `pool` CLI once context usage crosses a threshold
you pick, so you don't have to notice and type it yourself - and shows your
live context usage in a status bar at the bottom of the terminal, so you
always know where you're at:

```
auto-compress ctx 42% ████████░░░░░░░░░░░░ 110k/262k · compacts at 75%
```

## Requirements

- macOS, Linux, or WSL (the wrapper uses a Unix pseudo-terminal; no native Windows support)
- Python 3.8+ (stdlib only - nothing to `pip install`)
- `pool` CLI **1.0.16 or newer** (older versions have no hooks and reject the config), installed and working on its own before you add this on top

## Why it's built this way

`pool` (Poolside's CLI) has a native hooks system, but no hook payload
includes token counts or a context-usage percentage - and `/compact` is a
slash command handled by the interactive CLI layer, not something the model
can invoke on its own. So there's no way to do this purely from hooks.

Instead, `pool_autocompress.py` is a thin, transparent wrapper: it runs real
`pool` inside a pseudo-terminal, passes your keystrokes and its output
straight through untouched, and in the background watches the active
session's trajectory file for context usage. When you cross your threshold
and pool is sitting idle at an empty prompt, it types `/compact` and presses
Enter for you - exactly as if you'd done it - and says so in the status bar,
so it's never silent about what happened.

A tiny installed hook (`hooks/pool_hook.py`) is the only thing that runs
*inside* pool. It's registered on three of pool's own events:
- `SessionStart` - tells the wrapper which trajectory file belongs to the
  session it just spawned.
- `Stop` - tells the wrapper the agent finished its turn, so pool is idle and
  it's safe to type into it.
- `PreCompact` - tells the wrapper a compaction happened (auto or manual), so
  usage is measured from that point on.

## Setup

```bash
cd poolside_auto_compress
./install.sh
```

This will:
1. Append a marked, clearly-delimited hooks block (`SessionStart`, `Stop`,
   `PreCompact`) to
   `~/.config/poolside/settings.yaml` (backing up the original first). **If
   you already have a top-level `hooks:` key in that file, it won't touch it
   automatically** - a second top-level `hooks:` key would silently shadow
   your existing hooks in most YAML parsers. It'll print the exact snippet
   to merge in by hand instead.
2. Create `~/.config/poolside_auto_compress/config.json` from
   `config.example.json`.
3. Open the threshold picker (see below).

### Route `pool` through the wrapper (required)

Installing the hooks alone does **not** make `pool` use the wrapper - without
this step you're still running plain `pool` and nothing auto-compacts. Add an
alias to your shell's startup file (use `~/.zshrc` instead of `~/.bashrc` if
`echo $SHELL` ends in `zsh`, and adjust the path if you cloned the repo
somewhere other than your home folder):

```bash
echo 'alias pool="python3 $HOME/poolside_auto_compress/pool_autocompress.py"' >> ~/.bashrc
source ~/.bashrc
```

Terminals that were already open won't have the alias until you run
`source ~/.bashrc` in them (or open a new one). Everything else about your
`pool` session behaves identically - the wrapper is a passthrough.

### Confirm you're actually running through the wrapper

Typing `pool` launches the CLI either way, so it's not a test. Instead:

```bash
type pool
```

- `pool is aliased to 'python3 .../pool_autocompress.py'` - you're going
  through the wrapper. Once `pool` is open you'll also see the status bar
  (below) on the terminal's bottom row - that's the quickest visual check.
- `pool is /home/<you>/...` - you're running plain `pool`; the alias isn't
  loaded in this terminal (see above).

While a session is open, you can also confirm from a second terminal that a
per-session state file exists - only the wrapper creates these:

```bash
ls ~/.cache/poolside_auto_compress/    # expect session-<number>.json
```

## The status bar

While `pool` runs through the wrapper, the terminal's bottom row shows your
current context usage:

```
auto-compress ctx 42% ████████░░░░░░░░░░░░ 110k/262k · compacts at 75%
```

- **The percentage and bar** are how much of your model's context window
  (`context_window_tokens`, 262k by default) the current session is using.
- **Color** tells you how close you are: green, yellow once you're within
  80% of your threshold, red once you're over it.
- **`110k/262k`** is tokens used / window size.
- **`compacts at 75%`** is your configured threshold.
- **A `~` in front of the numbers** (`ctx ~42%`) means the wrapper couldn't
  find real token counts in pool's transcript and is using a rough
  file-size estimate instead (see the verification checklist).
- **`ctx -- (waiting for session)`** shows until pool's hooks report the
  session and the first usage check runs - usually a few seconds after startup.
- **When it auto-compacts**, the bar shows
  `76% context used (threshold 75%) - running /compact` for 5 seconds, then
  goes back to the usage display.

It works the same in any terminal (Windows Terminal, VS Code's terminal,
plain consoles): the wrapper tells `pool` its window is one row shorter and
keeps pool's scrolling above the bar, so pool never draws over it. Resizing
the window moves the bar with it, and it's removed cleanly when `pool`
exits. In very small windows (under 6 rows) the bar is skipped and pool
gets the full screen.

To turn it off, set `"status_bar": false` in
`~/.config/poolside_auto_compress/config.json` (auto-compact messages are
then printed as a line in the terminal instead). You can also show usage in
the terminal tab title with `"show_usage_in_title": true`, though many
terminals (e.g. VS Code's) don't display titles set by programs.

## Day-to-day operation

Once installed, just use `pool` as you always have (via the alias above) -
there's nothing else to run or remember.

- Keep an eye on the status bar (above) for where you're at.
- When your context usage crosses your configured threshold, `/compact` is
  submitted for you and the status bar says so for a few seconds.
- It only fires when pool is **idle at an empty prompt**: never mid-turn
  while the agent is working or waiting on a permission prompt, and never
  while you have a half-typed message (it would otherwise get glued onto
  your text). If you're over the threshold with something typed, it waits
  until you send it or clear the line (Ctrl+U / Ctrl+C).
- It won't fire again immediately after. It re-arms once pool confirms the
  compaction happened and usage is back under your threshold (or usage
  drops below `threshold_pct * rearm_ratio`), then fires again the next
  time you climb over it - with a minimum gap of `cooldown_seconds`
  regardless.
- To change your threshold later: `python3 configure.py` (or
  `python3 pool_autocompress.py --configure`) any time, including mid-project.
- To run plain `pool` without the wrapper for one session (e.g. to sanity
  check something), call it by its real path or `\pool` in shells where the
  alias would otherwise intercept it.
- If `pool` exits, the wrapper exits with it - there's no background daemon
  to remember to stop.

### Troubleshooting

- **"no session trajectory reported yet" message** - the hooks
  aren't registered (re-run `./install.sh`; if you have a pre-existing
  top-level `hooks:` key you may need to merge the snippet in by hand, see
  Setup) or you're on a version of `pool` that doesn't send `trajectory_path`
  in the `SessionStart` payload (check with the verification checklist below).
- **`/compact` never triggers** - make sure the `Stop` hook is registered
  too (without it the wrapper never sees pool go idle). Also check `context_window_tokens` in
  `config.json` isn't set way too high for your actual model, which would
  make the usage fraction always look small.
- **Nothing happens at all / no status bar** - check you're actually running
  through the wrapper (`type pool`, see Setup).
- **It compacts once and never again, fires at the wrong time, or the
  status bar's numbers look wrong** - turn on the debug log (see
  [Debug log](#debug-log) below) and check what the wrapper is seeing.
- **Status bar shows `ctx -- (waiting for session)` forever** - same cause
  as the "no session trajectory" message: the hooks aren't firing.
- **pool's screen looks off with the status bar on** (overlapping or
  missing lines at the bottom) - set `"status_bar": false` in `config.json`
  and let us know what you saw. The bar fences off the bottom row using
  standard terminal scroll regions; an app doing something unusual with
  the screen could still fight with it.
- **Garbled terminal after a crash** - if the wrapper dies uncleanly your
  terminal may be left in raw mode; run `reset` or `stty sane`.

## Picking your threshold

```bash
python3 configure.py
```

A small terminal menu with exactly four choices - **25% / 50% / 75% / 90%**
- navigated with the arrow keys, confirmed with Enter. Re-run it any time to
change your mind. `install.sh` runs this automatically on first setup, and
`pool_autocompress.py --configure` is a shortcut to it later.

| Threshold | Trade-off |
|---|---|
| 25% | Compacts early and often - least risk of hitting the wall, most interruptions |
| 50% | Balanced |
| 75% | Recommended default - compacts once you're deep into a session |
| 90% | Compacts late - fewer interruptions, least margin for error |

## Config reference (`~/.config/poolside_auto_compress/config.json`)

| Key | Meaning |
|---|---|
| `threshold_pct` | Set via `configure.py`, not by hand |
| `context_window_tokens` | Your model's real context window. The default is `262144` (256K, our on-prem model); change it if your model differs - the whole threshold calculation is relative to it |
| `poll_interval_seconds` | How often the background watcher checks usage |
| `cooldown_seconds` | Minimum time between auto-triggered compactions |
| `rearm_ratio` | Usage must drop back below `threshold_pct * rearm_ratio` before it's allowed to trigger again |
| `compact_command` | What gets injected - `/compact` by default; change only if your org's build uses a different command |
| `pool_binary` | What the wrapper execs - `pool` by default |
| `status_bar` | Show live context usage in a status bar on the terminal's bottom row (default `true`) |
| `debug_log` | Write a numbers-only trace of every usage check to `~/.cache/poolside_auto_compress/debug-<pid>.log` (default `false`) - see [Debug log](#debug-log) |
| `show_usage_in_title` | Also show it in the terminal tab title (default `false`; many terminals, e.g. VS Code's, don't display app-set titles) |

## Debug log

Off by default. When something isn't triggering the way you expect, the
debug log shows exactly what the wrapper saw and decided on every usage
check (every `poll_interval_seconds`).

**It contains no prompts, code, or file paths - only numbers, true/false
flags, and timestamps**, so it's safe to share with whoever is helping you
debug, even on machines handling sensitive/CUI work. (It's still worth
glancing over before you share it.)

### Turning it on and off

In `~/.config/poolside_auto_compress/config.json`:

```json
"debug_log": true
```

It takes effect the next time you start `pool`. **Set it back to `false`
when you're done** - the logs are not deleted automatically, and each
session writes a new file that keeps growing while the session runs.

### Where it goes

One file per wrapper session:

```
~/.cache/poolside_auto_compress/debug-<pid>.log
```

where `<pid>` is the wrapper's process ID (the same number as that
session's `session-<pid>.json`). To see the newest one live:

```bash
tail -f "$(ls -t ~/.cache/poolside_auto_compress/debug-*.log | head -1)"
```

To clean them up: `rm ~/.cache/poolside_auto_compress/debug-*.log`.

### What each line means

Each line is one JSON object, e.g.:

```json
{"t": 1790276790.6, "tokens": 118000, "exact": true, "frac": 0.45, "size": 912345, "offset": 0, "armed": true, "compacted_since_trigger": false, "idle": true, "last_stop_at": 1790276789.2, "last_compact_at": null, "last_trigger": null}
```

| Field | Meaning |
|---|---|
| `t` | When this check ran (Unix timestamp) |
| `tokens` | Estimated tokens currently in context |
| `exact` | `true` = real token counts from pool's transcript; `false` = rough file-size estimate (the `~` in the status bar) |
| `frac` | `tokens / context_window_tokens` - what's compared against your threshold |
| `size` | Transcript file size in bytes |
| `offset` | Byte position usage is measured from (the file size at the last compaction; `0` = whole file) |
| `armed` | `true` = allowed to auto-compact; `false` = already fired, waiting to re-arm |
| `compacted_since_trigger` | pool confirmed a compaction after our last auto-compact |
| `idle` | pool is idle at an empty prompt (the only time it will type `/compact`) |
| `last_stop_at` | When the agent last finished a turn (`Stop` hook) |
| `last_compact_at` | When pool last compacted (`PreCompact` hook); `null` = never this session |
| `last_trigger` | When the wrapper last auto-compacted; `null` = not yet |

### Reading it

- **Never fires**: is `frac` actually reaching your threshold (e.g. `0.75`)?
  If it is, look at `idle` - if it's always `false`, the `Stop` hook isn't
  firing, or you had text typed at the prompt. If `frac` stays low while
  pool's own display says you're high, `context_window_tokens` is probably
  wrong or the estimate is off (`exact: false`).
- **Fires once, never again**: after the first `/compact`,
  `last_compact_at` should get a value - if it stays `null`, the
  `PreCompact` hook isn't firing. `tokens` should also drop; if it stays
  high, the usage estimate isn't following pool's real context (see the
  verification checklist). `armed` flips back to `true` once usage is
  under the threshold after a confirmed compaction.
- **Numbers don't match pool's own display**: if `exact` is `false`, the
  wrapper is guessing from file size - the token field names in pool's
  transcript need adding to the wrapper (verification checklist, step 3).
- **No log file at all**: the file is only written once the session has
  been reported by the hooks, so check the "no session trajectory reported
  yet" item under Troubleshooting.

## Verification checklist (do this once, on a machine with `pool` installed)

The trajectory file's exact format isn't publicly documented, so before
trusting this at a real threshold:

1. Run `./install.sh`, confirm `pool` still launches normally afterward.
2. Start a plain `pool` session (not yet through the wrapper) and confirm
   `~/.cache/poolside_auto_compress/current_session.json` gets populated with
   a real `trajectory_path`, and that `last_stop_at` appears after the agent
   finishes a turn. Then start one **through the wrapper** and confirm a
   `session-<pid>.json` file appears in that same directory instead - that
   proves pool passes the wrapper's environment down to hooks. (If only
   `current_session.json` updates, the wrapper still works via a fallback,
   but two sessions launched at the same moment can mix up their state.)
3. `cat` that trajectory file and look at its actual shape. If it's JSON
   Lines with per-request usage stats under different key names than
   `pool_autocompress.py`'s `_USAGE_KEY_CANDIDATES` / `_USAGE_PAIR_CANDIDATES`
   guess, add the real key names there - it's a short list of strings. The
   number needs to be the size of the *latest request's* context, not a
   running session total (a running total never drops after `/compact`).
4. Run `python3 configure.py`, set the threshold very low (e.g. via a manual
   edit to `config.json` - the TUI itself only offers 25/50/75/90, which is
   plenty low for this test) and confirm a live `pool` session (via the
   wrapper) actually gets `/compact` injected and the status bar shows the
   "running /compact" message.
   Also confirm it waits while the agent is mid-turn and while you have
   text typed at the prompt.
5. Confirm it doesn't refire repeatedly right after compacting, and that it
   *does* fire again later in the same session once usage climbs back up
   (the rearm logic, which depends on the `PreCompact` hook).
6. Once the mechanism is confirmed working, set your real threshold with
   `configure.py`.

## Uninstall

```bash
./uninstall.sh
```

Removes the marked hook block from `settings.yaml` (backs it up first).
Leaves `~/.config/poolside_auto_compress/` in place; delete it yourself if
you want a clean slate.

## Known limitations

- **CLI only.** The VS Code "Poolside Assistant" extension is enterprise-gated
  and its internals aren't public, so this doesn't cover it.
- **macOS, Linux, or WSL only.** The PTY-wrapping approach doesn't work in
  native Windows shells (PowerShell / cmd) - run `pool` inside WSL.
- Usage estimation falls back to a coarse bytes/4 heuristic if the trajectory
  file's structured usage fields don't match what's expected - see the
  verification checklist above to confirm (and fix, if needed) the real
  format on your setup.
