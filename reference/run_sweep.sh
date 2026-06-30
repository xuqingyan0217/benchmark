#!/bin/bash
# ============================================================
# 手动参数扫描脚本（替代 vllm bench sweep serve，因 XPU 版不支持 sweep）
#
# 用法：
#   1. 确保端口 30015 未被占用
#   2. bash run_sweep.sh
#
# 结果保存在 ./results/ 目录下
# ============================================================
set -e

# ===== XPU 环境变量 =====
export VLLM_WORKER_MULTIPROC_METHOD=spawn
export XMLIR_ENABLE_MOCK_TORCH_COMPILE=false
export XMLIR_FORCE_USE_XPU_GRAPH=1
export ENABLE_VLLM_MOE_FC_SORTED=1
export ENABLE_VLLM_FAST_SWIGLU=1
export ENABLE_VLLM_FUSED_QKV_SPLIT_NORM_ROPE=1
export VLLM_XPU_CPU_BINDING='auto'
export CUDA_VISIBLE_DEVICES=0
export XMLIR_ENABLE_NEW_PG=1
export BKCL_TREE_THRESHOLD=0

MODEL_DIR="/data/models/Qwen3-30B-A3B"
MODEL_NAME="Qwen3-30B-A3B"
PORT=30015

# 使用 conda 环境中的 Python（避免 nohup 子进程找不到 vllm）
CONDA_ENV="/root/miniconda/envs/python310_torch251_cuda"
export PATH="${CONDA_ENV}/bin:${PATH}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULT_DIR="${SCRIPT_DIR}/results"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULT_CSV="${RESULT_DIR}/summary_${TIMESTAMP}.csv"

mkdir -p "$RESULT_DIR"

# 确保端口未被占用
if ss -tlnp | grep -q ":${PORT} "; then
    echo "ERROR: port ${PORT} already in use. Stop the running server first."
    exit 1
fi

# ===== 服务固定参数（不参与扫描）=====
FIXED_SERVE_ARGS="
    --host 0.0.0.0
    --port ${PORT}
    --model ${MODEL_DIR}
    --served-model-name ${MODEL_NAME}
    --dtype auto
    --trust-remote-code
    --tensor-parallel-size 1
    --block-size 128
    --max-model-len 40960
    --max-seq-len-to-capture 40960
    --distributed-executor-backend mp
    --enable-chunked-prefill
    --enable-prefix-caching
    --enable-auto-tool-choice --tool-call-parser hermes
    --enable-reasoning --reasoning-parser qwen3
    --disable-log-requests
    --gpu-memory-utilization 0.95
"

# ===== Bench 固定参数 =====
FIXED_BENCH_ARGS="
    --backend openai
    --base-url http://localhost:${PORT}
    --model ${MODEL_DIR}
    --served-model-name ${MODEL_NAME}
    --dataset-name random
    --num-prompts 500
    --ignore-eos
    --percentile-metrics ttft,tpot,itl,e2el
"

# ============================================================
# 扫描参数定义
# ============================================================

# 服务端扫描参数: max-num-seqs max-num-batched-tokens
SERVE_SWEEPS=(
    "16 32768 s1_baseline"
    "32 65536 s2_med_concurrency"
    "64 131072 s3_high_throughput"
    "8 16384 s4_low_concurrency"
    "128 131072 s5_extreme_seqs"
    "64 262144 s6_more_batched"
    "128 262144 s7_extreme_combo"
)

# Bench 扫描参数: input_len output_len request_rate
BENCH_SWEEPS=(
    "512 128 1 b1_short_low_qps"
    "512 128 4 b2_short_med_qps"
    "512 128 16 b3_short_high_qps"
    "2048 512 1 b4_med_low_qps"
    "2048 512 4 b5_med_med_qps"
    "4096 1024 1 b6_long_low_qps"
    "1024 256 inf b7_saturation"
)

# ============================================================
# 函数: 启动服务并等待就绪
# ============================================================
start_server() {
    local max_seqs=$1
    local max_tokens=$2
    local label=$3

    echo "========== Starting server: ${label} (max-seqs=${max_seqs}, max-tokens=${max_tokens}) =========="

    nohup python3 -m vllm.entrypoints.openai.api_server \
        ${FIXED_SERVE_ARGS} \
        --max-num-seqs ${max_seqs} \
        --max-num-batched-tokens ${max_tokens} \
        > "${RESULT_DIR}/server_${label}.log" 2>&1 &

    SERVER_PID=$!
    echo "Server PID: ${SERVER_PID}"

    # 等待服务就绪（最长等 10 分钟）
    echo "Waiting for server to be ready..."
    for i in $(seq 1 120); do
        if curl -s http://localhost:${PORT}/health > /dev/null 2>&1; then
            echo "Server is ready."
            return 0
        fi
        sleep 5
    done

    echo "ERROR: Server failed to become ready within 10 minutes."
    kill ${SERVER_PID} 2>/dev/null || true
    return 1
}

# ============================================================
# 函数: 停止服务
# ============================================================
stop_server() {
    echo "Stopping server (PID: ${SERVER_PID})..."
    kill ${SERVER_PID} 2>/dev/null || true
    # 等待进程退出
    for i in $(seq 1 30); do
        if ! kill -0 ${SERVER_PID} 2>/dev/null; then
            echo "Server stopped."
            return 0
        fi
        sleep 2
    done
    # 超时强制杀掉
    kill -9 ${SERVER_PID} 2>/dev/null || true
    echo "Server force-killed."
}

# ============================================================
# 主循环
# ============================================================

# 初始化结果 CSV
echo "serve_config,bench_config,req_rate,successful,ttft_mean_ms,ttft_p99_ms,tpot_mean_ms,tpot_p99_ms,itl_mean_ms,itl_p99_ms,e2el_mean_ms,e2el_p99_ms,total_tok_input+output,duration_s,total_tok_s" > "${RESULT_CSV}"

for serve_entry in "${SERVE_SWEEPS[@]}"; do
    read -r max_seqs max_tokens serve_label <<< "${serve_entry}"

    if ! start_server "${max_seqs}" "${max_tokens}" "${serve_label}"; then
        echo "Skipping all bench configs for ${serve_label} due to server failure."
        continue
    fi

    for bench_entry in "${BENCH_SWEEPS[@]}"; do
        read -r in_len out_len rate bench_label <<< "${bench_entry}"

        echo "----- Bench: ${serve_label}/${bench_label} (in=${in_len}, out=${out_len}, rate=${rate}) -----"

        set +e  # 允许 bench 命令失败，不影响后续
        RESULT=$(vllm bench serve \
            ${FIXED_BENCH_ARGS} \
            --random-input-len ${in_len} \
            --random-output-len ${out_len} \
            --request-rate ${rate} \
            2>&1)
        EXIT_CODE=$?
        set -e

        if [ ${EXIT_CODE} -ne 0 ]; then
            echo "Bench failed with exit code ${EXIT_CODE}, continuing..."
            echo "${serve_label},${bench_label},${rate},FAILED,,,,,,,,," >> "${RESULT_CSV}"
        else
            # 解析结果并写入 CSV
            echo "${RESULT}" | python3 "${SCRIPT_DIR}/parse_bench_result.py" "${serve_label}" "${bench_label}" "${RESULT_CSV}" || true
        fi

        # 重置缓存以便下一轮测试干净开始
        curl -s -X POST http://localhost:${PORT}/reset_prefix_cache > /dev/null 2>&1 || true

        sleep 2
    done

    stop_server
    sleep 5
done

echo "========== Sweep complete. Results: ${RESULT_CSV} =========="
