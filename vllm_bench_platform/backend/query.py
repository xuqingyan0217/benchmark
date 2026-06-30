"""后端 MVP 查询辅助函数。

这些函数先服务 CLI 和测试：状态来自 Kubernetes Job，结果文件来自 smoke 使用的
hostPath/PVC 目录。后续接入 HTTP 框架时可以直接复用这里的查询语义。

维护约束：
- 状态查询只读取 Master Job 和结果目录，不触发任何 Kubernetes 创建或删除动作。
- 不存在 run 时应由 kubectl 或文件读取错误明确暴露，避免返回伪造的空状态。
- `success_count` 来自 summary.jsonl 行数，因为 summary.csv 可能被用户用表格工具改动。
- `failed_count` 来自 failed_cases.jsonl 行数，和 master-controller 的重试策略解耦。
- hostPath smoke 模式下，后端和 Kubernetes 节点共享同一台机器时可以直接读结果目录。
- 如果部署到远端集群，结果下载入口需要通过 PVC 挂载、对象存储或专门的 artifact 服务重做。
- 这里返回 dict/list，是为了 CLI、测试和未来 HTTP API 都能直接序列化。
- 文件列表返回相对 run 根目录路径，避免把宿主机绝对路径暴露给普通调用方。
- 读取 failed cases 时保留原始 JSON 字段，不做二次 schema 转换，便于排障看到完整上下文。
- 查询逻辑不解析 raw logs/raw json；性能指标摘要由 master-controller 写入 summary。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vllm_bench_platform.backend.kubectl_client import KubectlRunner, run_kubectl


def get_run_status(
    run_id: str,
    namespace: str,
    results_root: str | Path,
    *,
    runner: KubectlRunner = run_kubectl,
) -> dict[str, Any]:
    """查询 Master Job 状态和结果目录摘要。"""
    job_name = f"vllm-bench-master-{run_id}"
    output = runner(
        ["kubectl", "get", "job", job_name, "-n", namespace, "-o", "json"],
        None,
        None,
    )
    job = json.loads(output)
    status = job.get("status", {})
    if status.get("succeeded"):
        phase = "Succeeded"
    elif status.get("failed"):
        phase = "Failed"
    else:
        phase = "Running"
    failed_cases = read_failed_cases(run_id, results_root)
    summary_path = Path(results_root) / run_id / "summary.jsonl"
    success_count = 0
    if summary_path.exists():
        success_count = sum(1 for line in summary_path.read_text(encoding="utf-8").splitlines() if line.strip())
    return {
        "run_id": run_id,
        "namespace": namespace,
        "status": phase,
        "master_job_name": job["metadata"]["name"],
        "start_time": status.get("startTime"),
        "end_time": status.get("completionTime"),
        "success_count": success_count,
        "failed_count": len(failed_cases),
        "result_path": str(Path(results_root) / run_id),
    }


def list_result_files(run_id: str, results_root: str | Path) -> list[str]:
    """列出 run 结果目录下的文件路径，返回相对 run 根目录路径。"""
    run_root = Path(results_root) / run_id
    if not run_root.exists():
        raise FileNotFoundError(f"run results not found: {run_root}")
    return sorted(str(path.relative_to(run_root)) for path in run_root.rglob("*") if path.is_file())


def read_failed_cases(run_id: str, results_root: str | Path) -> list[dict[str, Any]]:
    """读取 failed_cases.jsonl，文件不存在时返回空列表。"""
    path = Path(results_root) / run_id / "failed_cases.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
