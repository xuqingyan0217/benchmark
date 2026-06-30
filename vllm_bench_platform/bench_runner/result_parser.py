"""解析 vllm bench serve 文本输出。

reference/parse_bench_result.py 已经证明当前 vLLM 输出主要是文本表格；这里把同一口径
做成可复用函数，并保留缺失字段容忍度，避免不同 vllm-bench 版本轻微变动导致流程中断。

维护约束：
- parser 只提取 MVP summary 和 best_config 需要的指标，不试图覆盖 vllm-bench 所有输出。
- 缺失字段不会抛错；raw log/raw json 已经持久化，后续排障仍可查看原始输出。
- 字段命名统一使用 snake_case，避免把 CLI 输出中的缩写和空格传播到结果 JSON。
- `total_token_throughput` 是当前 best_config 的主要排序指标，必须稳定提取。
- E2EL 均值用于吞吐并列时的次级排序，因此也作为核心字段保留。
- 正则匹配文本输出而不是 JSON，是因为 reference 脚本和当前轻量 vllm-bench 输出就是文本。
- 如果后续 vllm-bench 支持结构化 JSON，应新增 parser 分支而不是删除文本 parser。
- 所有数值转换在这里完成，result_writer 不需要知道原始单位和文本格式。
"""

from __future__ import annotations

import re
from typing import Any


PATTERNS: dict[str, tuple[str, type]] = {
    "successful_requests": (r"Successful requests:\s+(\d+)", int),
    "duration_s": (r"Benchmark duration \(s\):\s+([\d.]+)", float),
    "total_input_tokens": (r"Total input tokens:\s+(\d+)", int),
    "total_generated_tokens": (r"Total generated tokens:\s+(\d+)", int),
    "request_rate": (r"Traffic request rate:\s+([\d.]+)", float),
    "ttft_mean_ms": (r"Mean TTFT \(ms\):\s+([\d.]+)", float),
    "ttft_p99_ms": (r"P99 TTFT \(ms\):\s+([\d.]+)", float),
    "tpot_mean_ms": (r"Mean TPOT \(ms\):\s+([\d.]+)", float),
    "tpot_p99_ms": (r"P99 TPOT \(ms\):\s+([\d.]+)", float),
    "itl_mean_ms": (r"Mean ITL \(ms\):\s+([\d.]+)", float),
    "itl_p99_ms": (r"P99 ITL \(ms\):\s+([\d.]+)", float),
    "e2el_mean_ms": (r"Mean E2EL \(ms\):\s+([\d.]+)", float),
    "e2el_p99_ms": (r"P99 E2EL \(ms\):\s+([\d.]+)", float),
    "total_token_throughput": (r"Total Token throughput \(tok/s\):\s+([\d.]+)", float),
}


def parse_vllm_bench_output(text: str) -> dict[str, Any]:
    """提取 summary 和 best_config 需要的核心指标。"""
    metrics: dict[str, Any] = {}
    for name, (pattern, caster) in PATTERNS.items():
        match = re.search(pattern, text)
        if match:
            metrics[name] = caster(match.group(1))
    return metrics
