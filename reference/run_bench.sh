#!/bin/bash
# ============================================================
# 单次 bench 测试（针对已运行的服务快速验证）
#
# 用法:
#   bash run_bench.sh              # 使用默认参数 (random, 1024/256, inf)
#   bash run_bench.sh 512 128 4    # 自定义 input_len output_len request_rate
# ============================================================
set -e

BASE_URL="${BASE_URL:-http://localhost:30015}"
MODEL_DIR="${MODEL_DIR:-/data/models/Qwen3-30B-A3B}"   # 本地模型路径（加载 tokenizer）
MODEL_NAME="${MODEL_NAME:-Qwen3-30B-A3B}"              # API 请求中的模型名
NUM_PROMPTS="${NUM_PROMPTS:-100}"

IN_LEN="${1:-1024}"
OUT_LEN="${2:-256}"
RATE="${3:-inf}"

echo "========== Bench: in=${IN_LEN}, out=${OUT_LEN}, rate=${RATE}, prompts=${NUM_PROMPTS} =========="

vllm bench serve \
    --backend openai \
    --base-url "${BASE_URL}" \
    --model "${MODEL_DIR}" \
    --served-model-name "${MODEL_NAME}" \
    --dataset-name random \
    --random-input-len "${IN_LEN}" \
    --random-output-len "${OUT_LEN}" \
    --request-rate "${RATE}" \
    --num-prompts "${NUM_PROMPTS}" \
    --ignore-eos \
    --percentile-metrics ttft,tpot,itl,e2el
