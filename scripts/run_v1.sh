#!/bin/bash
# Build the v1 corpus and label it with the local Qwen3.8 teacher (vLLM on :8000).
#   setsid nohup bash scripts/run_v1.sh > /mnt/ssd/ctxprune-data/v1/run.log 2>&1 &
set -eu
cd "$(dirname "$0")/.."
DATA=${DATA:-/mnt/ssd/ctxprune-data/v1}
TOTAL=${TOTAL:-10000}
echo "[$(date '+%F %T')] build corpus total=$TOTAL"
uv run python -u scripts/build_corpus.py --out "$DATA" --total "$TOTAL"
echo "[$(date '+%F %T')] label"
uv run python -u scripts/label.py --data "$DATA" --teacher openai --base-url http://127.0.0.1:8000/v1 \
  --model qwen38 --no-think --concurrency 48
echo "[$(date '+%F %T')] done"
