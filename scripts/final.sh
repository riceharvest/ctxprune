#!/bin/bash
# Final round: wait for v3 labels on all remaining chunks, free the GPU, train v1+v3 and v3-only,
# score with the independent DeepSeek reader (API) + quick eval. No teacher restart.
set -eu
cd "$(dirname "$0")/.."
DATA=${DATA:-/mnt/ssd/ctxprune-data/v1}
RUN=${RUN:-/mnt/ssd/ctxprune-data/runs/v1-final}
XPY=/mnt/ssd/b70-venv/bin/python
log() { echo "[$(date '+%F %T')] $*"; }
log "waiting for v3 labels"
until [ -f "$DATA/labeled/qwen38_v3.stats.json" ]; do sleep 20; done
log "stopping teacher (GPU free after training)"
docker rm -f ctxprune-teacher >/dev/null 2>&1 || true
sleep 5
RUNS=("v1v3|--labels qwen38_v1set qwen38_v3" "v3|--labels qwen38_v3")
for spec in "${RUNS[@]}"; do
  name=${spec%%|*}; extra=${spec#*|}
  log "train $name"
  # shellcheck disable=SC2086
  $XPY -u scripts/train.py --data "$DATA" --out "$RUN-$name" --epochs 5 --batch-size 32 \
    --base jhu-clsp/mmBERT-small --lr 5e-5 $extra
done
log "evals"
for spec in "${RUNS[@]}"; do
  name=${spec%%|*}
  uv run python -u scripts/eval_quick.py --data "$DATA" --model "$RUN-$name/final" --device cpu --no-baseline \
    > "$RUN-$name.quick.log" 2>&1 &
done
NAMES="v1v3 v3" RUN="$RUN" bash scripts/eval_external.sh
wait
log "done"
