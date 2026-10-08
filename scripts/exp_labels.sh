#!/bin/bash
# Label-type experiment on the same ~2.9k chunks: v1 (conservative, already trained as
# runs/v1-n3000-small), v3 (aggressive, explicit budget), v1+v3 (graded via both label sets).
# Waits for v3 labeling, trains on the B70, then quick eval + QA with Qwen and DeepSeek readers.
set -eu
cd "$(dirname "$0")/.."
DATA=${DATA:-/mnt/ssd/ctxprune-data/v1}
RUN=${RUN:-/mnt/ssd/ctxprune-data/runs/v1-n3000}
XPY=/mnt/ssd/b70-venv/bin/python
log() { echo "[$(date '+%F %T')] $*"; }

log "waiting for v3 labels"
until [ -f "$DATA/labeled/qwen38_v3.stats.json" ]; do sleep 20; done
grep -A3 '"swe_smith_tool"\|"toucan_mcp"\|"github_code"' "$DATA/labeled/qwen38_v3.stats.json" | head -20

log "stopping teacher"
docker rm -f ctxprune-teacher >/dev/null 2>&1 || true
sleep 5
RUNS=(
  "small-v3|--labels qwen38_v3"
  "small-v1v3|--labels qwen38 qwen38_v3"
)
for spec in "${RUNS[@]}"; do
  name=${spec%%|*}; extra=${spec#*|}
  log "train $name"
  # shellcheck disable=SC2086
  $XPY -u scripts/train.py --data "$DATA" --out "$RUN-$name" --epochs 5 --batch-size 32 \
    --base jhu-clsp/mmBERT-small --lr 5e-5 $extra
done
(for spec in "${RUNS[@]}"; do
   name=${spec%%|*}
   uv run python -u scripts/eval_quick.py --data "$DATA" --model "$RUN-$name/final" --device cpu --no-baseline \
     > "$RUN-$name.quick.log" 2>&1
 done) &
QPID=$!
bash scripts/teacher_up.sh
for spec in "${RUNS[@]}"; do
  name=${spec%%|*}
  log "QA $name (qwen)"
  uv run python -u scripts/eval_qa.py answer --data "$DATA" --model "$RUN-$name/final" --device cpu --no-baseline
done
NAMES="small-v3 small-v1v3" RUN="$RUN" bash scripts/eval_external.sh
wait $QPID || true
log "done (teacher left running)"
