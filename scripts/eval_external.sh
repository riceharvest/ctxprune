#!/bin/bash
# Re-score QA with an independent reader (DeepSeek V4 Flash via OpenRouter), so results don't depend
# on the teacher model also being the reader. CPU + API only.
# Reasoning on was checked on 40 questions (original and LLMLingua-2@0.5 contexts): identical exact
# match, 1.4-4x the cost, so it is off unless REASONING=1.
#   WAIT_PID=<checkpoint pid> RUN=... bash scripts/eval_external.sh
set -eu
cd "$(dirname "$0")/.."
DATA=${DATA:-/mnt/ssd/ctxprune-data/v1}
RUN=${RUN:-/mnt/ssd/ctxprune-data/runs/v1-n3000}
NAMES=${NAMES:-"small small-half base"}
[ -n "${WAIT_PID:-}" ] && while kill -0 "$WAIT_PID" 2>/dev/null; do sleep 30; done
if [ -z "${OPENROUTER_API_KEY:-}" ] && [ -f ~/.hermes/.env ]; then  # local fallback
  OPENROUTER_API_KEY=$(grep -h '^OPENROUTER_API_KEY=' ~/.hermes/.env | tail -1 | cut -d= -f2- | tr -d "\"'")
fi
export OPENROUTER_API_KEY=${OPENROUTER_API_KEY:?set OPENROUTER_API_KEY}
API=(--base-url https://openrouter.ai/api/v1 --llm deepseek/deepseek-v4-flash --api-key-env OPENROUTER_API_KEY
     --concurrency 24 --device cpu)
for name in $NAMES; do
  nb=$([ "$name" = small ] || echo --no-baseline)
  echo "[$(date '+%F %T')] $name: deepseek, reasoning off"
  uv run python -u scripts/eval_qa.py answer --data "$DATA" --model "$RUN-$name/final" $nb "${API[@]}"
  if [ "${REASONING:-0}" = 1 ]; then
    echo "[$(date '+%F %T')] $name: deepseek, reasoning on"
    uv run python -u scripts/eval_qa.py answer --data "$DATA" --model "$RUN-$name/final" $nb "${API[@]}" \
      --extra-body '{"reasoning":{"enabled":true}}' --max-answer-tokens 4096
  fi
done
echo "[$(date '+%F %T')] done"
