"""MVP 结果目录写入器。

维护约束：
- 所有最终 artifact 都必须写入 `/results/{run_id}`，不能把 `/work` 临时文件当结果。
- 初始化阶段创建 raw_json、raw_logs、server_logs、events 四个固定目录。
- summary.csv 给人工查看，summary.jsonl 给程序分析，两者需要记录同一 case 身份。
- failed_cases.jsonl 只写重试后仍失败或 target 级失败的 case。
- run_meta.json 记录 run 级上下文，方便脱离 Kubernetes 后仍能知道结果来源。
- writer 不负责解析 vllm-bench 输出，metrics 应由 master 内置执行器返回。
- writer 不负责选择 best_config，避免写入和分析职责耦合。
- append 方法使用追加模式，controller 崩溃时已完成 case 的记录仍保留。
- CSV 列固定，便于后续用户脚本消费；新增指标应同步更新测试和文档。
- failed case 的 ErrorType 枚举在写入时转为字符串，保证 JSONL 可直接查看。
- server logs/events 按 serve benchmark name 命名，因为 target 生命周期以 serve_config 为单位。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


SUMMARY_COLUMNS = [
    "run_id",
    "case_id",
    "serve_config",
    "bench_config",
    "target_endpoint",
    "attempt",
    "raw_json_path",
    "raw_log_path",
    "successful_requests",
    "ttft_mean_ms",
    "ttft_p99_ms",
    "tpot_mean_ms",
    "tpot_p99_ms",
    "itl_mean_ms",
    "itl_p99_ms",
    "e2el_mean_ms",
    "e2el_p99_ms",
    "total_token_throughput",
]


class ResultWriter:
    """管理 `/results/{run_id}` 的固定布局和追加写入。"""

    def __init__(self, results_root: str | Path, run_id: str):
        self.results_root = Path(results_root)
        self.run_id = run_id
        self.run_root = self.results_root / run_id

    def initialize(self, metadata: dict[str, Any]) -> None:
        self.run_root.mkdir(parents=True, exist_ok=True)
        for dirname in ("raw_json", "raw_logs", "server_logs", "events"):
            (self.run_root / dirname).mkdir(parents=True, exist_ok=True)
        (self.run_root / "run_meta.json").write_text(
            json.dumps({"run_id": self.run_id, **metadata}, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        if not (self.run_root / "summary.csv").exists():
            with (self.run_root / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS).writeheader()
        (self.run_root / "summary.jsonl").touch(exist_ok=True)
        (self.run_root / "failed_cases.jsonl").touch(exist_ok=True)

    def append_summary(self, record: dict[str, Any]) -> None:
        metrics = dict(record.get("metrics", {}))
        row = {
            "run_id": record["run_id"],
            "case_id": record["case_id"],
            "serve_config": record["serve_config"],
            "bench_config": record["bench_config"],
            "target_endpoint": record["target_endpoint"],
            "attempt": record["attempt"],
            "raw_json_path": record.get("raw_json_path", ""),
            "raw_log_path": record.get("raw_log_path", ""),
            **{name: metrics.get(name, "") for name in SUMMARY_COLUMNS},
        }
        for key in ("run_id", "case_id", "serve_config", "bench_config", "target_endpoint", "attempt", "raw_json_path", "raw_log_path"):
            row[key] = record.get(key, row.get(key, ""))
        with (self.run_root / "summary.csv").open("a", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS).writerow(row)
        with (self.run_root / "summary.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def append_failed_case(self, record: dict[str, Any]) -> None:
        payload = dict(record)
        error_type = payload.get("error_type")
        if hasattr(error_type, "value"):
            payload["error_type"] = error_type.value
        with (self.run_root / "failed_cases.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def write_server_log(self, serve_name: str, text: str) -> None:
        (self.run_root / "server_logs" / f"{serve_name}.log").write_text(text, encoding="utf-8")

    def write_events(self, serve_name: str, text: str) -> None:
        (self.run_root / "events" / f"{serve_name}.json").write_text(text, encoding="utf-8")
