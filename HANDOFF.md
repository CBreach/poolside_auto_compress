# Handoff: poolside_auto_compress

For an agent picking this up cold. Read this before touching the code.

## What this is and why it exists

The user's company uses Poolside (`pool` CLI, on-prem AI coding tool) and
runs out of context frequently. They had to notice and manually type
`/compact` to recover, which they found annoying. They asked for a tool that
auto-runs `/compact` at a threshold, modeled loosely on how the Hermes agent
auto-compacts. They explicitly want per-engineer configurable thresholds, and
specifically asked for that configuration to happen through **a single TUI
offering exactly four choices: 25% / 50% / 75% / 90%** (not a freeform
number, not hand-edited config).

Scope, confirmed with the user during planning:
- **CLI (`pool`) only for v1.** The VS Code "Poolside Assistant" extension is
  enterprise-gated/closed-source; its internals (whether it shares config
  with the CLI) are not publicly documented, so it was explicitly deferred.
- The literal command is `/compact` (user confirmed directly; earlier they'd
  said "/compress" loosely but that's not real).
- Full history and the reasoning behind the architecture decision (why not
  just use `pool`'s own hooks/config knobs, why a PTY wrapper) is in the plan
  file this was built from:
  `~/.claude/plans/hey-caludio-lets-get-woolly-ripple.md`. Read that if you
  need the "why" behind a decision that isn't repeated here.

## The core constraint that shapes everything

This was the hard part to get right, so don't miss it:

- `pool` has a native hooks system (`PreToolUse`, `PostToolUse`,
  `UserPromptSubmit`, `Stop`, `PreCompact`, `SessionStart`), confirmed from
  Poolside's own docs (`docs.poolside.ai/hooks`, `github.com/poolsideai/pool`).
- **No hook payload includes token counts or a context-usage percentage.**
  Confirmed by reading the actual documented input schema.
- `/compact` is a slash command handled at the CLI/interactive layer (same as
  Claude Code's `/compact`) - it is **not** a tool the model can invoke, and
  hooks operate on model-facing lifecycle events. A hook literally cannot
  "run `/compact`" by itself.

So there is no way to do this with hooks alone. The design instead:
1. Wraps `pool` in a PTY (`pool_autocompress.py`), transparently passing
   stdin/stdout through, so it behaves identically to plain `pool` from the
   user's perspective.
2. Uses one small installed hook script (`hooks/pool_hook.py`) registered
   on three events, writing to a per-wrapper state file (path passed via the
   `POOL_AUTOCOMPRESS_STATE` env var, falling back to a shared
   `current_session.json` if pool doesn't pass env through to hooks):
   - `SessionStart`: which trajectory file belongs to this session (every
     hook payload includes `trajectory_path`).
   - `Stop`: agent turn finished, pool is idle at the prompt.
   - `PreCompact`: compaction happened; records the trajectory's byte size
     at that moment so usage is measured only from there on.
3. A background thread in the wrapper watches that trajectory file, estimates
   context usage from it, and when the configured threshold is crossed *and*
   pool is idle (last `Stop` is newer than the user's last Enter, and the
   user has nothing half-typed), writes `/compact`, waits 150ms, then writes
   `\r` (Enter) into the PTY's master fd - i.e. it simulates the keystrokes a
   human would type. This is the only part of the design that can actually
   trigger real compaction.

## Known unknown - not yet verified against a real `pool` install

**The trajectory file's on-disk format is not publicly documented.** This
sandbox has no `pool` binary and no real TTY, so the usage-parsing logic in
`pool_autocompress.py` (`_structured_usage_from_text`,
`_USAGE_KEY_CANDIDATES`, `_USAGE_PAIR_CANDIDATES`) was written defensively
against plausible JSON-Lines shapes (looking for `usage.total_tokens`,
`usage.input_tokens`/`usage.output_tokens`, etc.) with a coarse bytes/4
heuristic fallback if none of those keys match. **This is the single biggest
risk in the whole design** and is called out explicitly in the README's
"Verification checklist" section. If you have access to a real `pool`
install, running that checklist (steps 1-3 especially: start a session, look
at the real trajectory file, adjust the key-name guesses if needed) should be
the first thing you do before trusting this for real, and before making any
other change to the usage-estimation code.

Also unverified for the same reason (no real `pool` binary available while
building this):
- That `/compact` + `\r` written into the PTY master fd is treated by
  `pool` exactly as if it were typed. `\r` (not `\n`) is what a raw-mode
  terminal sends for Enter; a bare `\n` arrives as Ctrl+J, which TUIs treat
  as "insert newline" - the original version of this tool sent `\n` and
  would most likely never have submitted.
- That pool passes its environment down to hook processes (needed for the
  per-wrapper state file; there's a fallback if it doesn't).
- That `Stop` fires at the end of every agent turn, and that `matcher: "*"`
  is accepted on `Stop`/`PreCompact` (the docs only say matcher is
  "per event").

## What IS verified (all done in this sandbox, see file history for exact
commands run)

- End-to-end against a fake `pool` (a raw-mode Python TUI in a PTY that
  fires the hook script the way real pool would and appends usage to a
  trajectory): no injection mid-turn, injection once the turn's `Stop`
  fires, no injection while text is half-typed, injection after the line is
  cleared, rearm after a compaction, clean exit status passthrough, state
  file cleaned up on exit.
- `estimate_usage_fraction()` correctly computes the fraction both via the
  structured-key path and the bytes/4 heuristic fallback, tested against
  synthetic trajectory files.
- `configure.py`'s config load/save round-trips correctly (the curses UI
  itself couldn't be driven headlessly, but its I/O layer was tested).
- `configure.py` now degrades gracefully (prints current setting, doesn't
  crash) when there's no real TTY - this was a real bug caught during testing
  (an uncaught `_curses.error` traceback) and fixed.
- `install.sh` was tested against three scenarios: fresh install (no
  `settings.yaml` yet), idempotent re-run (correctly no-ops), and - most
  important - **a pre-existing top-level `hooks:` key in `settings.yaml`**,
  which it correctly refuses to touch automatically (prints a manual-merge
  snippet instead) rather than risk silently shadowing the user's existing
  hooks via a duplicate top-level YAML key.
- `uninstall.sh` was tested to cleanly remove exactly the marked block it
  added, leaving the rest of the file untouched.

## File map

| File | Role |
|---|---|
| `pool_autocompress.py` | The wrapper. PTY spawn of `pool`, passthrough I/O, background usage-watcher thread that injects `/compact`. |
| `configure.py` | Curses TUI, 4 fixed choices (25/50/75/90), writes `threshold_pct` to config. Also importable as a module by `pool_autocompress.py --configure`. |
| `hooks/pool_hook.py` | Installed pool hook for `SessionStart` / `Stop` / `PreCompact`. Records trajectory path, idle-ness, and compaction offset to the wrapper's state file. Never fails, never prints. |
| `install.sh` | Idempotent installer. Marker-delimited append to `settings.yaml`, refuses to auto-merge if a top-level `hooks:` key already exists, sets up config dir, launches the TUI. |
| `uninstall.sh` | Removes exactly the marked block `install.sh` added. |
| `config.example.json` | Template copied to `~/.config/poolside_auto_compress/config.json` on install. |
| `README.md` | User-facing install/operate/troubleshoot doc. |

State/config this tool reads and writes at runtime (not in the repo):
- `~/.config/poolside/settings.yaml` — pool's own global settings; we only
  ever touch the marked block we add.
- `~/.config/poolside_auto_compress/config.json` — our own config.
- `~/.cache/poolside_auto_compress/session-<wrapper pid>.json` — ephemeral,
  written by the hook, read by the wrapper, deleted on exit.
- `~/.cache/poolside_auto_compress/current_session.json` — only written when
  the hook runs without the wrapper's env var (plain `pool`, or pool not
  passing env through). Debugging aid and fallback.

## Revision history

- **2026-09-24 review pass.** Fixed before the tool was ever run against real
  `pool`: `\n` -> `\r` for Enter; injection now waits for pool to be idle
  with an empty prompt (was: could fire mid-turn or glue onto half-typed
  text); per-wrapper state file instead of one shared file (was: a startup
  race where a fast `SessionStart` could be missed entirely, plus concurrent
  sessions clobbering each other, plus a 30s give-up); `PreCompact`-based
  offset so the trigger re-arms after compaction (was: with an append-only
  trajectory or a cumulative counter it could only ever fire once per
  session); usage search now finds nested `usage` dicts and OpenAI-style
  `prompt_tokens`/`completion_tokens` (likely for an on-prem vLLM-style
  backend); banner uses `\r\n` for the raw-mode terminal; output writes are
  locked so banners can't split pool's escape sequences.

## If you're picking this up to continue it

Priority order:
1. Run the README's verification checklist against a real `pool` install.
   Fix the usage-key guesses in `pool_autocompress.py` if the real trajectory
   format doesn't match.
2. Confirm the `/compact\n` PTY injection actually triggers real compaction
   in a live session (not just that the bytes get written).
3. Only then consider VS Code extension coverage - it was explicitly
   deferred, not attempted, and its integration surface (does it share
   `settings.yaml`/hooks with the CLI via the ACP protocol, or is it fully
   separate?) is still an open question, not something already scoped out.

Don't restructure the hooks-can't-invoke-slash-commands / PTY-injection
architecture without re-confirming the underlying constraint - it's the
reason this isn't a simpler hooks-only solution, and it was arrived at from
reading Poolside's actual docs, not assumed.
