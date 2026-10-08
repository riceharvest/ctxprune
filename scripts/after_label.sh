#!/bin/bash
# After labeling: free the GPU, train on the B70, quick eval, restart the teacher, QA eval.
#   setsid nohup bash scripts/after_label.sh > /mnt/ssd/ctxprune-data/v1/after.log 2>&1 &
set -eu
cd "$(dirname "$0")/.."
DATA=${DATA:-/mnt/ssd/ctxprune-data/v1}
RUN=${RUN:-/mnt/ssd/ctxprune-data/runs/v1-mmbert-small}
XPY=/mnt/ssd/b70-venv/bin/python
log() { echo "[$(date '+%F %T')] $*"; }

log "waiting for labeling and question generation"
until grep -q "\] done" "$DATA/run.log" 2>/dev/null; do sleep 30; done
while pgrep -f "eval_qa.py questions" >/dev/null; do sleep 15; done

log "stopping teacher"
docker rm -f ctxprune-teacher >/dev/null 2>&1 || true
sleep 5

log "train (100% of labels)"
$XPY -u scripts/train.py --data "$DATA" --out "$RUN" --epochs 3 --batch-size 32 --lr 5e-5
log "train (50% of labels, learning-curve point: does more labeling help?)"
$XPY -u scripts/train.py --data "$DATA" --out "$RUN-half" --epochs 3 --batch-size 32 --lr 5e-5 --train-frac 0.5

log "quick eval (CPU) in background; teacher restart"
(uv run python -u scripts/eval_quick.py --data "$DATA" --model "$RUN/final" --device cpu \
   > "$DATA/eval_quick.log" 2>&1
 uv run python -u scripts/eval_quick.py --data "$DATA" --model "$RUN-half/final" --device cpu --no-baseline \
   > "$DATA/eval_quick_half.log" 2>&1) &
QPID=$!
bash scripts/teacher_up.sh

log "QA eval"
uv run python -u scripts/eval_qa.py answer --data "$DATA" --model "$RUN/final" --device cpu
uv run python -u scripts/eval_qa.py answer --data "$DATA" --model "$RUN-half/final" --device cpu --no-baseline
wait $QPID || true
log "done"
