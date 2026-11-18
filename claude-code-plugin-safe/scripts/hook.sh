#!/bin/sh
# ctxprune-safe: run the bundled stdlib hook (scripts/rt/) with the first working Python 3.9+. No pip, no network.
# No Python found -> exit 0 with no output: Claude Code keeps the original tool output.
HERE=$(dirname "$0")
case "${OS:-}" in Windows_NT) CANDIDATES="py python python3" ;; *) CANDIDATES="python3 python" ;; esac
for py in $CANDIDATES; do
  if command -v "$py" >/dev/null 2>&1; then
    if [ "$py" = py ]; then exec py -3 "$HERE/hook.py"; fi
    exec "$py" "$HERE/hook.py"
  fi
done
cat >/dev/null 2>&1
exit 0
