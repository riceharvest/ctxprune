#!/bin/sh
# ctxprune plugin 0.4.1: runs `ctxprune hook claude-code` (the safe preset; cross-turn dedup by model: claude-opus-* on) from the
# installed CLI, else via uvx pinned to the 0.3-0.4 lines. With neither available it exits quietly, and Claude Code keeps
# the original output. A console script never puts the project dir on sys.path, so a project's own files can't
# replace ctxprune's modules.
if command -v ctxprune >/dev/null 2>&1; then
  exec ctxprune hook claude-code
elif command -v uvx >/dev/null 2>&1; then
  exec uvx --quiet --from "ctxprune>=0.3,<0.5" ctxprune hook claude-code
fi
cat >/dev/null 2>&1
exit 0
