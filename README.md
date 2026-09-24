# poolside_auto_compress

Auto-runs `/compact` in the `pool` CLI once context usage crosses a threshold
you pick, so you don't have to notice and type it yourself.

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
Enter for you - exactly as if you'd done it - and prints a one-line banner so
it's never silent about what happened.

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
  through the wrapper.
- `pool is /home/<you>/...` - you're running plain `pool`; the alias isn't
  loaded in this terminal (see above).

While a session is open, you can also confirm from a second terminal that a
per-session state file exists - only the wrapper creates these:

```bash
ls ~/.cache/poolside_auto_compress/    # expect session-<number>.json
```

## Day-to-day operation

Once installed, just use `pool` as you always have (via the alias above) -
there's nothing else to run or remember.

- Your terminal tab's title shows live context usage, e.g.
  `pool · ctx 42% ▓▓▓▓░░░░░░ 110k/262k · compacts at 75%`. A `~` in front
  of the numbers means it's the rough bytes/4 estimate, not real token
  counts from the transcript (see the verification checklist). Turn it off
  with `"show_usage_in_title": false`.
- When your context usage crosses your configured threshold, you'll see a
  banner like `[auto-compress] 76% context used (threshold 75%) - running
  /compact` and `/compact` is submitted for you.
- It only fires when pool is **idle at an empty prompt**: never mid-turn
  while the agent is working or waiting on a permission prompt, and never
  while you have a half-typed message (it would otherwise get glued onto
  your text). If you're over the threshold with something typed, it waits
  until you send it or clear the line (Ctrl+U / Ctrl+C).
- It won't fire again immediately after - it waits until usage drops back
  down (below `threshold_pct * rearm_ratio`) and climbs back up, with a
  minimum gap of `cooldown_seconds` regardless.
- To change your threshold later: `python3 configure.py` (or
  `python3 pool_autocompress.py --configure`) any time, including mid-project.
- To run plain `pool` without the wrapper for one session (e.g. to sanity
  check something), call it by its real path or `\pool` in shells where the
  alias would otherwise intercept it.
- If `pool` exits, the wrapper exits with it - there's no background daemon
  to remember to stop.

### Troubleshooting

- **"no session trajectory reported yet" banner** - the hooks
  aren't registered (re-run `./install.sh`; if you have a pre-existing
  top-level `hooks:` key you may need to merge the snippet in by hand, see
  Setup) or you're on a version of `pool` that doesn't send `trajectory_path`
  in the `SessionStart` payload (check with the verification checklist below).
- **`/compact` never triggers** - make sure the `Stop` hook is registered
  too (without it the wrapper never sees pool go idle). Also check `context_window_tokens` in
  `config.json` isn't set way too high for your actual model, which would
  make the usage fraction always look small.
- **Nothing happens at all / no tab title** - check you're actually running
  through the wrapper (`type pool`, see Setup).
- **Tab title shows `ctx --` forever** - same cause as the "no session
  trajectory" banner: the hooks aren't firing.
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
| `show_usage_in_title` | Show live context usage in the terminal tab title (default `true`) |

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
   wrapper) actually gets `/compact` injected and the banner prints.
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
