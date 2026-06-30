"""解析 vllm-bench 结果。

Rust vllm-bench 使用 `--save-result` 输出 JSON，同时控制台文本保持和 Python
`vllm bench serve` 接近的指标名称。这里优先从 JSON 中提取 summary 字段，缺失时回退到
文本解析，保证 raw artifact 仍然可用于排障。
"""

from __future__ import annotations

import re
from typing import Any


TEXT_PATTERNS: dict[str, tuple[str, type]] = {
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

JSON_ALIASES: dict[str, tuple[str, ...]] = {
    "successful_requests": ("successful_requests", "completed", "num_successful_requests"),
    "duration_s": ("duration_s", "benchmark_duration_s", "duration"),
    "total_input_tokens": ("total_input_tokens", "total_prompt_tokens"),
    "total_generated_tokens": ("total_generated_tokens", "total_output_tokens"),
    "request_rate": ("request_rate",),
    "ttft_mean_ms": ("ttft_mean_ms", "mean_ttft_ms"),
    "ttft_p99_ms": ("ttft_p99_ms", "p99_ttft_ms"),
    "tpot_mean_ms": ("tpot_mean_ms", "mean_tpot_ms"),
    "tpot_p99_ms": ("tpot_p99_ms", "p99_tpot_ms"),
    "itl_mean_ms": ("itl_mean_ms", "mean_itl_ms"),
    "itl_p99_ms": ("itl_p99_ms", "p99_itl_ms"),
    "e2el_mean_ms": ("e2el_mean_ms", "mean_e2el_ms"),
    "e2el_p99_ms": ("e2el_p99_ms", "p99_e2el_ms"),
    "total_token_throughput": (
        "total_token_throughput",
        "total_token_throughput_tok_s",
        "total_tokens_per_second",
        "total_tok_s",
    ),
}


def parse_bench_metrics(payload: dict[str, Any] | None, text: str = "") -> dict[str, Any]:
    """提取 summary 和 best_config 需要的核心指标。"""
    metrics = _parse_json_metrics(payload or {})
    for key, value in _parse_text_metrics(text).items():
        metrics.setdefault(key, value)
    return metrics


def _parse_json_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    flattened = _flatten(payload)
    metrics: dict[str, Any] = {}
    for output_name, aliases in JSON_ALIASES.items():
        for alias in aliases:
            if alias in flattened:
                metrics[output_name] = flattened[alias]
                break
    return metrics


def _parse_text_metrics(text: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for name, (pattern, caster) in TEXT_PATTERNS.items():
        match = re.search(pattern, text)
        if match:
            metrics[name] = caster(match.group(1))
    return metrics


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).replace("-", "_").replace(" ", "_").lower()
            child_prefix = f"{prefix}_{normalized}" if prefix else normalized
            flattened.update(_flatten(item, child_prefix))
            if not isinstance(item, dict | list):
                flattened.setdefault(normalized, item)
    elif isinstance(value, list):
        return flattened
    else:
        flattened[prefix] = value
    return flattened
