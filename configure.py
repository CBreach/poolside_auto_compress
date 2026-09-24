#!/usr/bin/env python3
"""Curses TUI for picking the auto-compress context threshold.

Exactly four choices are offered on purpose (25% / 50% / 75% / 90%) so the
setting stays a deliberate, easy-to-reason-about pick rather than an
arbitrary number every engineer tunes differently for no reason.
"""
import curses
import json
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "poolside_auto_compress"
CONFIG_PATH = CONFIG_DIR / "config.json"
EXAMPLE_PATH = Path(__file__).resolve().parent / "config.example.json"

CHOICES = [25, 50, 75, 90]
DESCRIPTIONS = {
    25: "Compact early and often - smallest risk of hitting the wall, most interruptions",
    50: "Balanced - compacts well before things get tight",
    75: "Recommended default - compacts once you're deep into a session",
    90: "Compact late - fewer interruptions, less margin before running out",
}


def _load_or_init_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            pass
    if EXAMPLE_PATH.exists():
        return json.loads(EXAMPLE_PATH.read_text())
    return {"threshold_pct": 75}


def _save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def _draw(stdscr, selected_idx: int, current_pct: int):
    stdscr.erase()
    stdscr.addstr(0, 0, "poolside_auto_compress - pick your auto-/compact threshold", curses.A_BOLD)
    stdscr.addstr(1, 0, "Up/Down to move, Enter to confirm, q to cancel")
    stdscr.addstr(2, 0, f"Current setting: {current_pct}%")
    for i, pct in enumerate(CHOICES):
        row = 4 + i * 2
        marker = "> " if i == selected_idx else "  "
        attr = curses.A_REVERSE if i == selected_idx else curses.A_NORMAL
        stdscr.addstr(row, 0, f"{marker}{pct:>3}%", attr)
        stdscr.addstr(row, 10, DESCRIPTIONS[pct])
    stdscr.refresh()


def _run_tui(stdscr) -> int:
    curses.curs_set(0)
    cfg = _load_or_init_config()
    current_pct = cfg.get("threshold_pct", 75)
    selected_idx = CHOICES.index(current_pct) if current_pct in CHOICES else 2

    while True:
        _draw(stdscr, selected_idx, current_pct)
        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            selected_idx = (selected_idx - 1) % len(CHOICES)
        elif key in (curses.KEY_DOWN, ord("j")):
            selected_idx = (selected_idx + 1) % len(CHOICES)
        elif key in (curses.KEY_ENTER, 10, 13):
            cfg["threshold_pct"] = CHOICES[selected_idx]
            _save_config(cfg)
            return cfg["threshold_pct"]
        elif key in (ord("q"), 27):
            return current_pct


def main():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        cfg = _load_or_init_config()
        print(
            "[auto-compress] no interactive terminal available, skipping the "
            f"threshold picker. Current setting: {cfg.get('threshold_pct', 75)}%. "
            f"Run 'python3 {Path(__file__).name}' to pick one interactively, "
            f"or edit threshold_pct in {CONFIG_PATH} directly."
        )
        return
    chosen = curses.wrapper(_run_tui)
    print(f"[auto-compress] threshold set to {chosen}% (saved to {CONFIG_PATH})")


if __name__ == "__main__":
    sys.exit(main())
