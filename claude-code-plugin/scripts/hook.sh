#!/bin/sh
# Runs `ctxprune hook claude-code`: the installed CLI if there is one, else via uvx.
# With neither available it exits quietly, and Claude Code keeps the original output.
if command -v ctxprune >/dev/null 2>&1; then
  exec ctxprune hook claude-code
elif command -v uvx >/dev/null 2>&1; then
  exec uvx --quiet --from "ctxprune>=0.2" ctxprune hook claude-code
fi
cat >/dev/null 2>&1
exit 0
