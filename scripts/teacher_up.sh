#!/bin/bash
# Start the Qwen3.8-27B GPTQ teacher on the B70 (vLLM XPU) at 127.0.0.1:8000 and wait until healthy.
# Settings are the ones that proved stable: >64 seqs crashed with UR_RESULT_ERROR_DEVICE_LOST.
set -eu
NAME=${NAME:-ctxprune-teacher}
MODEL_DIR=${MODEL_DIR:-/mnt/ssd/models/qwen38-27b-gptq}
COOKBOOK=/mnt/ssd/b70-cookbook
docker rm -f "$NAME" >/dev/null 2>&1 || true
RGID="$(stat -c '%g' /dev/dri/render* | sort -u | sed -n '1p')"
docker run -d --name "$NAME" -p 127.0.0.1:8000:8000 --device /dev/dri --group-add "$RGID" \
  -v /dev/dri:/dev/dri:ro -v "$MODEL_DIR:/model:ro" \
  -v "$COOKBOOK/patches/patch_mtp_nightly.py:/patch_mtp.py:ro" \
  -v "$COOKBOOK/patches/patch_mtp_boundary.py:/patch_boundary.py:ro" \
  -e VLLM_TARGET_DEVICE=xpu -e ZE_FLAT_DEVICE_HIERARCHY=COMPOSITE -e ZE_AFFINITY_MASK=0 \
  -e B70_MTP_BF16_DRAFT=1 -e VLLM_XPU_ENABLE_XPU_GRAPH=1 -e PYTORCH_ALLOC_CONF=expandable_segments:True \
  --entrypoint bash 'vllm/vllm-openai-xpu@sha256:f01e24f6c7ff01f1e0662234255a1372297d1dbd89d003cf13c8fad3eab1ba4f' -lc \
  "set -e; python /patch_mtp.py; python /patch_boundary.py; exec vllm serve /model --quantization gptq --dtype float16 --max-model-len 8192 --gpu-memory-utilization 0.90 --kv-cache-dtype fp8 --port 8000 --max-num-seqs 64 --max-num-batched-tokens 8192 --no-enable-prefix-caching --served-model-name qwen38 --language-model-only" >/dev/null
for i in $(seq 1 80); do
  curl -sf -m 3 http://127.0.0.1:8000/health >/dev/null && { echo "teacher up after $((i * 15))s"; exit 0; }
  sleep 15
done
echo "teacher did not become healthy"; docker logs --tail 30 "$NAME"; exit 1
