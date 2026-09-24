#!/usr/bin/env bash
# Removes the poolside_auto_compress marked hooks block from settings.yaml.
# Only touches text between the markers install.sh added; if you merged the
# hook entries by hand (because a top-level hooks: key already existed), this
# script can't find it and won't try - remove those entries yourself.
set -euo pipefail

SETTINGS="$HOME/.config/poolside/settings.yaml"
MARK_BEGIN="# BEGIN poolside_auto_compress"
MARK_END="# END poolside_auto_compress"

if [ ! -f "$SETTINGS" ] || ! grep -qF "$MARK_BEGIN" "$SETTINGS"; then
  echo "[uninstall] no marked block found in $SETTINGS - nothing to remove."
  echo "(If you merged the hook entries by hand, remove it manually.)"
  exit 0
fi

cp "$SETTINGS" "$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"
awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
  $0 == b { skip=1; next }
  $0 == e { skip=0; next }
  !skip { print }
' "$SETTINGS" > "$SETTINGS.tmp"
mv "$SETTINGS.tmp" "$SETTINGS"

echo "[uninstall] removed the marked hook block from $SETTINGS (backup saved alongside it)."
echo "[uninstall] config at ~/.config/poolside_auto_compress/ was left in place; delete it if you want a clean slate."
