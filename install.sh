#!/usr/bin/env bash
# Installs the poolside_auto_compress hooks (SessionStart, Stop, PreCompact)
# into the engineer's global pool settings.yaml, then walks them through the
# threshold TUI.
#
# Safe by construction: this script NEVER rewrites settings.yaml with a YAML
# parser. It only appends a clearly marked block, and refuses to touch the
# file at all if a top-level `hooks:` key already exists there (a second
# top-level `hooks:` key would silently shadow the user's existing hooks in
# most YAML parsers - so we print the snippet to merge by hand instead).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POOLSIDE_DIR="$HOME/.config/poolside"
SETTINGS="$POOLSIDE_DIR/settings.yaml"
HOOK_CMD="python3 $DIR/hooks/pool_hook.py"
MARK_BEGIN="# BEGIN poolside_auto_compress"
MARK_END="# END poolside_auto_compress"

chmod +x "$DIR/pool_autocompress.py" "$DIR/configure.py" "$DIR/hooks/pool_hook.py"

mkdir -p "$POOLSIDE_DIR"
touch "$SETTINGS"

if grep -qF "$MARK_BEGIN" "$SETTINGS" 2>/dev/null; then
  echo "[install] hooks already present in $SETTINGS - skipping insert."
elif grep -qE '^hooks:' "$SETTINGS" 2>/dev/null; then
  cat <<EOF
[install] $SETTINGS already has a top-level 'hooks:' key.

Auto-inserting a second one would silently shadow your existing hooks, so
this script won't touch the file. Please add the following entries under your
EXISTING 'hooks:' key by hand (create any of these event lists that don't
exist yet). All three are required:

  SessionStart:
    - name: poolside_auto_compress
      matcher: "*"
      command: "$HOOK_CMD"
      timeout: 5
  Stop:
    - name: poolside_auto_compress
      matcher: "*"
      command: "$HOOK_CMD"
      timeout: 5
  PreCompact:
    - name: poolside_auto_compress
      matcher: "*"
      command: "$HOOK_CMD"
      timeout: 5

(This prompt will show again on every re-run, since a hand-merged entry has
no marker for this script to detect - just press Enter to skip past it.)
EOF
  read -r -p "Press Enter once you've added them manually, or Ctrl+C to stop: " _
else
  cp "$SETTINGS" "$SETTINGS.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
  {
    echo ""
    echo "$MARK_BEGIN"
    echo "hooks:"
    echo "  SessionStart:"
    echo "    - name: poolside_auto_compress"
    echo "      matcher: \"*\""
    echo "      command: \"$HOOK_CMD\""
    echo "      timeout: 5"
    echo "  Stop:"
    echo "    - name: poolside_auto_compress"
    echo "      matcher: \"*\""
    echo "      command: \"$HOOK_CMD\""
    echo "      timeout: 5"
    echo "  PreCompact:"
    echo "    - name: poolside_auto_compress"
    echo "      matcher: \"*\""
    echo "      command: \"$HOOK_CMD\""
    echo "      timeout: 5"
    echo "$MARK_END"
  } >> "$SETTINGS"
  echo "[install] hooks appended to $SETTINGS (backup saved alongside it)."
fi

CONFIG_DIR="$HOME/.config/poolside_auto_compress"
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.json" ]; then
  cp "$DIR/config.example.json" "$CONFIG_DIR/config.json"
  echo "[install] wrote default config to $CONFIG_DIR/config.json"
fi

echo "[install] pick your auto-/compact threshold:"
python3 "$DIR/configure.py"

cat <<EOF

[install] done. Two things worth doing next:

1. Check $CONFIG_DIR/config.json's "context_window_tokens" matches your
   on-prem model's real context window - the default (200000) is a
   placeholder.
2. Use the wrapper instead of pool directly, e.g. add to your shell rc:
     alias pool="python3 $DIR/pool_autocompress.py"
EOF
