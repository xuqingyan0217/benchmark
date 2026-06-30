"""
解析 vllm bench serve 的输出，提取关键指标并追加到 CSV。

用法（由 run_sweep.sh 调用）:
    echo "$VLLM_OUTPUT" | python3 parse_bench_result.py <serve_label> <bench_label> <csv_path>
"""
import sys
import re
import os

serve_label = sys.argv[1]
bench_label = sys.argv[2]
csv_path = sys.argv[3]

text = sys.stdin.read()

def extract(pattern, text):
    m = re.search(pattern, text)
    return m.group(1) if m else ""

# 从输出中提取指标（根据实际输出格式）
successful    = extract(r'Successful requests:\s+(\d+)', text)
duration      = extract(r'Benchmark duration \(s\):\s+([\d.]+)', text)
total_input   = extract(r'Total input tokens:\s+(\d+)', text)
total_output  = extract(r'Total generated tokens:\s+(\d+)', text)
req_throughput = extract(r'Request throughput \(req/s\):\s+([\d.]+)', text)
output_tps    = extract(r'Output token throughput \(tok/s\):\s+([\d.]+)', text)
total_tps     = extract(r'Total Token throughput \(tok/s\):\s+([\d.]+)', text)
req_rate      = extract(r'Traffic request rate:\s+([\d.]+)', text)

ttft_mean     = extract(r'Mean TTFT \(ms\):\s+([\d.]+)', text)
ttft_median   = extract(r'Median TTFT \(ms\):\s+([\d.]+)', text)
ttft_p99      = extract(r'P99 TTFT \(ms\):\s+([\d.]+)', text)

tpot_mean     = extract(r'Mean TPOT \(ms\):\s+([\d.]+)', text)
tpot_median   = extract(r'Median TPOT \(ms\):\s+([\d.]+)', text)
tpot_p99      = extract(r'P99 TPOT \(ms\):\s+([\d.]+)', text)

itl_mean      = extract(r'Mean ITL \(ms\):\s+([\d.]+)', text)
itl_median    = extract(r'Median ITL \(ms\):\s+([\d.]+)', text)
itl_p99       = extract(r'P99 ITL \(ms\):\s+([\d.]+)', text)

e2el_mean     = extract(r'Mean E2EL \(ms\):\s+([\d.]+)', text)
e2el_median   = extract(r'Median E2EL \(ms\):\s+([\d.]+)', text)
e2el_p99      = extract(r'P99 E2EL \(ms\):\s+([\d.]+)', text)

row = (
    f"{serve_label},{bench_label},{req_rate},{successful},"
    f"{ttft_mean},{ttft_p99},"
    f"{tpot_mean},{tpot_p99},"
    f"{itl_mean},{itl_p99},"
    f"{e2el_mean},{e2el_p99},"
    f"{total_input}+{total_output},{duration},{total_tps}"
)

with open(csv_path, "a") as f:
    f.write(row + "\n")

print(f"[parsed] {row}")
