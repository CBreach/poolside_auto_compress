#!/usr/bin/env python3
"""Transparent PTY wrapper around `pool` that auto-injects /compact at a threshold.

Usage:
    pool_autocompress.py [--configure] [pool-args...]

Everything other than the wrapper's own flag is passed straight through to `pool`.
Run `pool_autocompress.py --configure` on its own to open the threshold TUI.
"""
import fcntl
import json
import os
import pty
import select
import signal
import struct
import sys
import termios
import threading
import time
import tty
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "poolside_auto_compress"
CONFIG_PATH = CONFIG_DIR / "config.json"
STATE_DIR = Path.home() / ".cache" / "poolside_auto_compress"
STATE_ENV = "POOL_AUTOCOMPRESS_STATE"  # must match hooks/pool_hook.py
# Where the hook writes if pool doesn't pass our env var through to hooks.
SHARED_STATE_PATH = STATE_DIR / "current_session.json"
DEFAULT_CONFIG = {
    "threshold_pct": 75,
    "context_window_tokens": 200000,
    "poll_interval_seconds": 5,
    "cooldown_seconds": 120,
    "rearm_ratio": 0.8,
    "compact_command": "/compact",
    "pool_binary": "pool",
    "show_usage_in_title": True,
}

# Raw-mode terminals send Enter as a carriage return. A bare "\n" arrives as
# Ctrl+J, which TUIs treat as "insert newline", not "submit".
SUBMIT_KEY = b"\r"
# Gap between typing the command and pressing Enter, so the TUI doesn't treat
# the whole thing as one pasted blob (where \r becomes a literal newline).
SUBMIT_DELAY_SECONDS = 0.15
# Keys that leave the prompt line empty: Enter, Ctrl+C, Ctrl+U.
_LINE_CLEARING_KEYS = (b"\r", b"\x03", b"\x15")
# The title is only written once pool's output has been quiet this long, so it
# can never land in the middle of one of pool's own escape sequences.
TITLE_QUIET_SECONDS = 0.3
# Rewrite the title this often even if unchanged, in case pool set its own.
TITLE_REASSERT_SECONDS = 10
# xterm title stack: save the user's title on start, restore it on exit.
TITLE_PUSH = b"\x1b[22;0t"
TITLE_POP = b"\x1b[23;0t"


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except (json.JSONDecodeError, OSError) as exc:
            sys.stderr.write(f"[auto-compress] warning: failed to read {CONFIG_PATH}: {exc}\n")
    return cfg


def estimate_usage_fraction(trajectory_path: Path, context_window_tokens: int, offset: int = 0):
    """Best-effort estimate of context usage as a fraction of the window, or None."""
    usage = estimate_usage_tokens(trajectory_path, offset)
    if usage is None:
        return None
    return min(usage[0] / context_window_tokens, 1.0)


def estimate_usage_tokens(trajectory_path: Path, offset: int = 0):
    """Best-effort (tokens, exact) for the live context, or None if unreadable.

    `exact` is True when the count came from real usage stats in the
    trajectory, False when it's the bytes/4 guess. Only the part of the trajectory after `offset` (the file size at the most
    recent compaction, recorded by the PreCompact hook) is considered, since
    everything before it was summarized away. Tries a structured parse first
    (looking for per-request usage stats in the trajectory's JSON Lines), then
    falls back to a coarse bytes/4 heuristic.
    """
    try:
        with trajectory_path.open("rb") as f:
            f.seek(offset)
            text = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return None

    structured = _structured_usage_from_text(text)
    if structured is not None:
        return structured, True

    # Fallback heuristic: ~4 bytes per token, applied to the live transcript.
    return len(text) / 4, False


# Per-request context size. Deliberately no session-cumulative counters here:
# those never drop after /compact, so the trigger would never re-arm.
_USAGE_KEY_CANDIDATES = (
    "context_tokens",
    "context_length_used",
    "total_tokens",
)
# (prompt-side, completion-side) pairs: Anthropic-style, then OpenAI/vLLM-style.
_USAGE_PAIR_CANDIDATES = (
    ("input_tokens", "output_tokens"),
    ("prompt_tokens", "completion_tokens"),
)
# Anthropic-style prompt caching reports cached input separately from input_tokens.
_CACHE_KEYS = ("cache_read_input_tokens", "cache_creation_input_tokens")


def _usage_from_dict(usage: dict):
    for key in _USAGE_KEY_CANDIDATES:
        if isinstance(usage.get(key), (int, float)):
            return usage[key]
    for pair in _USAGE_PAIR_CANDIDATES:
        if all(isinstance(usage.get(k), (int, float)) for k in pair):
            cached = sum(usage[k] for k in _CACHE_KEYS if isinstance(usage.get(k), (int, float)))
            return sum(usage[k] for k in pair) + cached
    return None


def _find_usage(obj, depth: int = 0):
    """Depth-first search for a usage-shaped dict anywhere inside a JSON record."""
    if depth > 6:
        return None
    if isinstance(obj, dict):
        value = _usage_from_dict(obj)
        if value is not None:
            return value
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        value = _find_usage(child, depth + 1)
        if value is not None:
            return value
    return None


def _structured_usage_from_text(text: str):
    """Scan JSON Lines from the end for the most recent request's token usage."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line or line[0] not in "{[":
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        value = _find_usage(obj)
        if value is not None:
            return value
    return None


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def read_state(state_path: Path, wrapper_started_at: float) -> dict:
    """This wrapper's own state file, or the shared fallback if hooks never saw our env var.

    The fallback is only trusted for sessions that started after this wrapper
    did; with two wrappers launched at once it can still pick up the wrong one,
    which is why the per-wrapper file is preferred.
    """
    state = _read_json(state_path)
    if state:
        return state
    shared = _read_json(SHARED_STATE_PATH)
    if shared.get("started_at", 0.0) >= wrapper_started_at:
        return shared
    return {}


class Session:
    """What the passthrough loop knows about the prompt line, shared with the watcher."""

    def __init__(self, master_fd: int, out_fd: int):
        self.master_fd = master_fd
        self.out_fd = out_fd
        self.lock = threading.Lock()
        self.last_submit_at = 0.0
        self.last_output_at = 0.0
        self.pending_input = False  # user has typed something they haven't sent yet

    def on_user_input(self, data: bytes):
        with self.lock:
            if data.startswith(b"\x1b"):
                pass  # arrow keys / escape sequences: don't change the line state
            elif data.endswith(_LINE_CLEARING_KEYS):
                self.pending_input = False
                if data.endswith(b"\r"):
                    self.last_submit_at = time.time()
            else:
                self.pending_input = True
            os.write(self.master_fd, data)

    def write_output(self, data: bytes):
        with self.lock:
            os.write(self.out_fd, data)
            self.last_output_at = time.time()

    def set_title(self, title: str) -> bool:
        """Set the terminal tab title if pool is between writes; False if it wasn't."""
        with self.lock:
            if time.time() - self.last_output_at < TITLE_QUIET_SECONDS:
                return False
            os.write(self.out_fd, f"\x1b]0;{title}\x07".encode())
            return True

    def banner(self, message: str):
        # \r\n because the real terminal is in raw mode (no \n -> \r\n translation).
        self.write_output(f"\r\n[auto-compress] {message}\r\n".encode())

    def is_idle(self, state: dict) -> bool:
        """pool is waiting at an empty prompt: last turn finished, nothing half-typed."""
        with self.lock:
            return (
                not self.pending_input
                and state.get("last_stop_at", 0.0) >= self.last_submit_at
            )

    def inject(self, command: str):
        # Deliberately doesn't bump last_submit_at: /compact isn't an agent turn,
        # so no Stop hook follows it and we'd consider pool busy forever. The
        # watcher's armed/cooldown state is what prevents re-injection instead.
        with self.lock:
            os.write(self.master_fd, command.encode())
        time.sleep(SUBMIT_DELAY_SECONDS)
        with self.lock:
            os.write(self.master_fd, SUBMIT_KEY)


def format_title(tokens, exact: bool, window: int, threshold_pct: int) -> str:
    """e.g. 'pool · ctx 42% ▓▓▓▓░░░░░░ 110k/262k · compacts at 75%' (~ marks a guess)."""
    if tokens is None:
        return f"pool · ctx -- · compacts at {threshold_pct}%"
    frac = min(tokens / window, 1.0)
    filled = round(frac * 10)
    bar = "▓" * filled + "░" * (10 - filled)
    approx = "" if exact else "~"
    return (
        f"pool · ctx {approx}{frac*100:.0f}% {bar} "
        f"{approx}{tokens/1000:.0f}k/{window/1000:.0f}k · compacts at {threshold_pct}%"
    )


def watcher_loop(
    session: Session, state_path: Path, started: float, show_title: bool, stop_event: threading.Event
):
    cfg = load_config()
    window = cfg["context_window_tokens"]
    threshold = cfg["threshold_pct"] / 100.0
    rearm_at = threshold * cfg["rearm_ratio"]
    armed = True
    last_trigger = 0.0
    warned_no_session = False

    # Tick faster than the usage poll so a title that couldn't be written
    # (pool was mid-output) gets retried soon, not a whole poll later.
    tick = min(1.0, cfg["poll_interval_seconds"])
    next_poll = 0.0
    title = format_title(None, False, window, cfg["threshold_pct"])
    title_written, title_written_at = None, 0.0

    while not stop_event.wait(tick):
        now = time.time()
        if show_title and (title != title_written or now - title_written_at >= TITLE_REASSERT_SECONDS):
            if session.set_title(title):
                title_written, title_written_at = title, now
        if now < next_poll:
            continue
        next_poll = now + cfg["poll_interval_seconds"]

        # Re-read every poll: the trajectory can change on resume, and the
        # Stop/PreCompact hooks keep updating idle state and the compaction offset.
        state = read_state(state_path, started)
        trajectory = state.get("trajectory_path")
        if not trajectory:
            if not warned_no_session and time.time() - started > 60:
                session.banner(
                    "no session trajectory reported yet - is the hook installed? "
                    "(see README troubleshooting). Still watching."
                )
                warned_no_session = True
            continue

        usage = estimate_usage_tokens(Path(trajectory), state.get("compact_offset", 0))
        if usage is None:
            continue
        tokens, exact = usage
        frac = min(tokens / window, 1.0)
        title = format_title(tokens, exact, window, cfg["threshold_pct"])
        if not armed:
            if frac <= rearm_at:
                armed = True
            continue
        if (
            frac >= threshold
            and time.time() - last_trigger >= cfg["cooldown_seconds"]
            and session.is_idle(state)
        ):
            session.banner(
                f"{frac*100:.0f}% context used (threshold {cfg['threshold_pct']}%) "
                f"- running {cfg['compact_command']}"
            )
            session.inject(cfg["compact_command"])
            last_trigger = time.time()
            armed = False


def _real_winsize():
    """Packed TIOCSWINSZ struct for the user's real terminal, or None if there isn't one."""
    try:
        size = os.get_terminal_size()  # (columns, lines) - note the order
    except OSError:
        return None
    return struct.pack("HHHH", size.lines, size.columns, 0, 0)


def _set_winsize(fd: int, winsize=None):
    winsize = winsize or _real_winsize()
    if winsize is not None:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


def run(argv):
    cfg = load_config()
    pool_binary = cfg["pool_binary"]
    child_argv = [pool_binary] + argv

    # One state file per wrapper process, handed to the hooks via the
    # environment pool passes down to them, so concurrent sessions stay apart.
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path = STATE_DIR / f"session-{os.getpid()}.json"
    try:
        state_path.unlink()
    except FileNotFoundError:
        pass
    os.environ[STATE_ENV] = str(state_path)
    started = time.time()  # before fork, so the child's SessionStart can't beat it

    old_attrs = None
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    is_tty = os.isatty(stdin_fd)
    winsize = None
    if is_tty:
        old_attrs = termios.tcgetattr(stdin_fd)
        winsize = _real_winsize()

    pid, master_fd = pty.fork()
    if pid == 0:
        # Give pool's terminal the real terminal's settings and size *before*
        # it starts, so its first render isn't laid out for a 0x0 window.
        try:
            if old_attrs is not None:
                termios.tcsetattr(0, termios.TCSANOW, old_attrs)
            if winsize is not None:
                _set_winsize(0, winsize)
        except (OSError, termios.error):
            pass
        try:
            os.execvp(child_argv[0], child_argv)
        except OSError as exc:
            sys.stderr.write(f"[auto-compress] failed to launch {child_argv[0]!r}: {exc}\n")
        os._exit(127)

    if is_tty:
        tty.setraw(stdin_fd)

        def _on_resize(signum, frame):
            _set_winsize(master_fd)

        signal.signal(signal.SIGWINCH, _on_resize)

    show_title = bool(cfg["show_usage_in_title"]) and os.isatty(stdout_fd)
    if show_title:
        os.write(stdout_fd, TITLE_PUSH)

    session = Session(master_fd, stdout_fd)
    stop_event = threading.Event()
    watcher = threading.Thread(
        target=watcher_loop,
        args=(session, state_path, started, show_title, stop_event),
        daemon=True,
    )
    watcher.start()

    try:
        while True:
            try:
                rlist, _, _ = select.select([stdin_fd, master_fd], [], [])
            except InterruptedError:
                continue
            if stdin_fd in rlist:
                data = os.read(stdin_fd, 4096)
                if not data:
                    break
                session.on_user_input(data)
            if master_fd in rlist:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    data = b""
                if not data:
                    break
                session.write_output(data)
    finally:
        stop_event.set()
        if show_title:
            with session.lock:
                os.write(stdout_fd, TITLE_POP)
        if old_attrs is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attrs)
        for path in (state_path, state_path.with_name(state_path.name + ".tmp")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    _, status = os.waitpid(pid, 0)
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--configure":
        from configure import main as configure_main  # local import, same directory

        configure_main()
        return 0
    return run(argv)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
