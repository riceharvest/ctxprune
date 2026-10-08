#!/bin/bash
# Learning-curve checkpoint while labeling runs: once N chunks are labeled, pause labeling,
# train on all labels so far and on half of them, evaluate both, and leave the teacher up
# so labeling can resume (its cache makes resuming free).
#   N=3000 setsid nohup bash scripts/checkpoint.sh > $DATA/checkpoint_3000.log 2>&1 &
set -eu
cd "$(dirname "$0")/.."
DATA=${DATA:-/mnt/ssd/ctxprune-data/v1}
N=${N:-3000}
RUN=${RUN:-/mnt/ssd/ctxprune-data/runs/v1-n$N}
XPY=/mnt/ssd/b70-venv/bin/python
CACHE=$DATA/teacher_cache/qwen38.jsonl
log() { echo "[$(date '+%F %T')] $*"; }

log "waiting for $N labeled chunks"
until [ "$(wc -l < "$CACHE")" -ge "$N" ]; do sleep 20; done
log "pausing labeling"
pkill -f "python -u scripts/label.py --data $DATA --teacher openai" || true
while pgrep -f "eval_qa.py questions" >/dev/null; do sleep 15; done
uv run python -u scripts/label.py --data "$DATA" --teacher openai --model qwen38 --cache-only | tail -45

log "stopping teacher"
docker rm -f ctxprune-teacher >/dev/null 2>&1 || true
sleep 5
# Runs: name | extra train.py args. small-all vs small-half is the "is less labeling enough"
# test; base-all checks whether a bigger backbone is worth it.
RUNS=(
  "small|--base jhu-clsp/mmBERT-small --lr 5e-5"
  "small-half|--base jhu-clsp/mmBERT-small --lr 5e-5 --train-frac 0.5"
  "base|--base jhu-clsp/mmBERT-base --lr 3e-5"
)
for spec in "${RUNS[@]}"; do
  name=${spec%%|*}; extra=${spec#*|}
  log "train $name"
  # shellcheck disable=SC2086
  $XPY -u scripts/train.py --data "$DATA" --out "$RUN-$name" --epochs ${EPOCHS:-5} --batch-size 32 $extra
done

(for spec in "${RUNS[@]}"; do
   name=${spec%%|*}
   nb=$([ "$name" = small ] || echo --no-baseline)
   uv run python -u scripts/eval_quick.py --data "$DATA" --model "$RUN-$name/final" --device cpu $nb \
     > "$RUN-$name.quick.log" 2>&1
 done) &
QPID=$!
bash scripts/teacher_up.sh
log "QA eval"
for spec in "${RUNS[@]}"; do
  name=${spec%%|*}
  nb=$([ "$name" = small ] || echo --no-baseline)
  uv run python -u scripts/eval_qa.py answer --data "$DATA" --model "$RUN-$name/final" --device cpu $nb
done
wait $QPID || true
log "done (teacher left running)"
