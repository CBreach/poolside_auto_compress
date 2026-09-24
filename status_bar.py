"""Bottom-row status bar for the pool wrapper.

The bar lives on the terminal's last row. pool is told the window is one row
shorter, and the terminal's scroll region (DECSTBM) is limited to the rows
above the bar, so pool's own scrolling never touches it. This works the same
in any terminal (Windows Terminal, VS Code, plain consoles), unlike the tab
title, which some terminals hide or let the app overwrite.

pool's output passes through `OutputFilter`, which:
- rewrites "reset the scroll region" sequences so they stop above the bar
  (from pool's point of view the region is still its whole screen), and
- notices sequences that wipe the bar (clears, full reset, switching to or
  from the alternate screen) so the bar gets redrawn.
"""
import re

SAVE_CURSOR = b"\x1b7"
RESTORE_CURSOR = b"\x1b8"

_INTERESTING = re.compile(
    rb"\x1b\[(\d*)(?:;(\d*))?r"  # DECSTBM: set scroll region
    rb"|\x1bc"  # RIS: full terminal reset
    rb"|\x1b\[\?(?:1049|1047|47)[hl]"  # switch to / from the alternate screen
    rb"|\x1b\[[023]?J"  # erase below cursor / whole screen / scrollback
)
# An escape sequence cut off at the end of a read: lone ESC or unfinished CSI.
_PARTIAL_TAIL = re.compile(rb"\x1b(?:\[[0-9;?]*)?\Z")


def scroll_region(rows: int) -> bytes:
    """Limit scrolling to the rows above the bar, leaving the cursor where it was."""
    return SAVE_CURSOR + b"\x1b[1;%dr" % (rows - 1) + RESTORE_CURSOR


def render(rows: int, cols: int, segments) -> bytes:
    """Draw `segments` ([(sgr, text), ...]) on the bottom row, leaving the cursor where it was.

    Stops one column short of the edge: writing the last column can make
    some terminals wrap or scroll.
    """
    limit = max(cols - 1, 0)
    used = 0
    parts = []
    for sgr, text in segments:
        text = text[: limit - used]
        if not text:
            break
        parts.append(b"\x1b[0;%sm" % sgr.encode() + text.encode())
        used += len(text)
    return (
        SAVE_CURSOR
        + b"\x1b[%d;1H\x1b[0m\x1b[2K" % rows
        + b"".join(parts)
        + b"\x1b[0m"
        + RESTORE_CURSOR
    )


def teardown(rows: int) -> bytes:
    """Give the whole screen back: full-height scroll region, bottom row blanked."""
    return SAVE_CURSOR + b"\x1b[r" + b"\x1b[%d;1H\x1b[2K" % rows + RESTORE_CURSOR


class OutputFilter:
    """Rewrites pool's output so it stays out of the bar's row. Not thread-safe."""

    def __init__(self, rows: int):
        self.rows = rows
        self.held = b""  # tail of the last read that might be half an escape sequence
        self.bar_dirty = True

    def feed(self, data: bytes) -> bytes:
        data = self.held + data
        self.held = b""
        partial = _PARTIAL_TAIL.search(data)
        if partial:
            self.held = data[partial.start():]
            data = data[: partial.start()]
        return _INTERESTING.sub(self._rewrite, data)

    def flush(self) -> bytes:
        """Release a held tail as-is (called when pool goes quiet mid-sequence)."""
        data, self.held = self.held, b""
        return data

    def _rewrite(self, match) -> bytes:
        seq = match.group(0)
        pool_rows = self.rows - 1
        if seq.endswith(b"r"):
            top = int(match.group(1) or 1)
            bottom = min(int(match.group(2) or pool_rows), pool_rows)
            if top >= bottom:
                top, bottom = 1, pool_rows
            return b"\x1b[%d;%dr" % (top, bottom)
        self.bar_dirty = True
        if seq.endswith(b"J"):
            return seq  # just wiped the bar; it's redrawn once pool goes quiet
        # Full reset and screen switches can also drop the scroll region.
        return seq + scroll_region(self.rows)
