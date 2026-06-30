"""MVP best_config 分析。

维护约束：
- MVP 只做确定性简单选择，不做 Pareto、高级加权或跨模型比较。
- 主要排序指标是 total_token_throughput，符合 reference summary 中的吞吐优先口径。
- 吞吐并列时选择 e2el_mean_ms 更低的 case，避免同吞吐下响应时间更差。
- 没有成功 case 时仍写 best_config.json，调用方不需要猜测文件是否存在。
- analyzer 只读取 summary/failed 文件，不接触 Kubernetes 或 raw logs。
- 输出保留 selected_case 原始 summary 记录，方便用户追溯 serve/bench 配置和 raw 路径。
- 后续如需更复杂分析，应新增字段而不是改变 `has_successful_case` 语义。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_best_config(run_root: str | Path) -> dict[str, Any]:
    """按 total_token_throughput 最大、e2el_mean_ms 最小选择最优成功 case。"""
    root = Path(run_root)
    summary_path = root / "summary.jsonl"
    cases = []
    if summary_path.exists():
        cases = [json.loads(line) for line in summary_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not cases:
        failed_path = root / "failed_cases.jsonl"
        failed_count = 0
        if failed_path.exists():
            failed_count = sum(1 for line in failed_path.read_text(encoding="utf-8").splitlines() if line.strip())
        payload = {
            "has_successful_case": False,
            "selected_case": None,
            "failed_count": failed_count,
        }
    else:
        selected = max(
            cases,
            key=lambda item: (
                float(item.get("metrics", {}).get("total_token_throughput", 0) or 0),
                -float(item.get("metrics", {}).get("e2el_mean_ms", 0) or 0),
            ),
        )
        payload = {
            "has_successful_case": True,
            "selected_case": selected,
            "selection_metrics": {
                "total_token_throughput": selected.get("metrics", {}).get("total_token_throughput"),
                "e2el_mean_ms": selected.get("metrics", {}).get("e2el_mean_ms"),
            },
        }
    (root / "best_config.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return payload
