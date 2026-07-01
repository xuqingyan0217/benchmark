"""master-controller 内置的 vllm-bench 执行器。

新架构不再启动独立 bench-runner 容器，也不暴露 localhost HTTP agent。Master 容器直接
调用镜像内的 `vllm-bench` 二进制，并把 raw log/raw json 写入 `/results/{run_id}`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
from typing import Any, Callable
from urllib.parse import urlparse

from vllm_bench_platform.backend.runtime_config import normalize_bench_params
from vllm_bench_platform.master.result_parser import parse_bench_metrics


ProcessRunner = Callable[..., Any]


@dataclass(frozen=True)
class BenchRunRequest:
    """一次 benchmark case 的执行请求。"""

    target_endpoint: str
    run_id: str
    serve_benchmark_name: str
    bench_benchmark_name: str
    bench_params: dict[str, Any] = field(default_factory=dict)
    model_path: str = ""
    served_model_name: str = ""

    @property
    def case_id(self) -> str:
        return f"{self.serve_benchmark_name}-{self.bench_benchmark_name}"


class DirectBenchRunner:
    """供 master-controller 调用的内置 runner。"""

    def __init__(
        self,
        *,
        results_root: str | Path,
        work_dir: str | Path,
        bench_binary: str = "vllm-bench",
        timeout_seconds: int = 1800,
        num_prompts: int = 10,
        process_runner: ProcessRunner | None = None,
    ):
        self.results_root = Path(results_root)
        self.work_dir = Path(work_dir)
        self.bench_binary = bench_binary
        self.timeout_seconds = timeout_seconds
        self.num_prompts = num_prompts
        self.process_runner = process_runner

    def run_bench(self, payload: dict[str, Any]) -> dict[str, Any]:
        """执行 master-controller 传入的 benchmark case payload。"""
        return run_bench_case(
            BenchRunRequest(
                target_endpoint=payload["target_endpoint"],
                run_id=payload["run_id"],
                serve_benchmark_name=payload["serve_benchmark_name"],
                bench_benchmark_name=payload["bench_benchmark_name"],
                bench_params=dict(payload.get("bench_params", {})),
                model_path=payload.get("model_path", ""),
                served_model_name=payload.get("served_model_name", ""),
            ),
            results_root=self.results_root,
            work_dir=self.work_dir,
            bench_binary=self.bench_binary,
            timeout_seconds=self.timeout_seconds,
            num_prompts=self.num_prompts,
            runner=self.process_runner,
        )


def is_localhost_endpoint(endpoint: str) -> bool:
    """避免误把 master 容器自身当成 target vLLM 服务。"""
    host = urlparse(endpoint).hostname
    return host in {"localhost", "127.0.0.1", "::1"}


def build_vllm_bench_command(
    request: BenchRunRequest,
    *,
    bench_binary: str = "vllm-bench",
    num_prompts: int = 10,
    result_dir: str | Path | None = None,
    result_filename: str | None = None,
) -> list[str]:
    """构造 Rust vllm-bench 命令。"""
    params = normalize_bench_params(request.bench_params)
    command = [
        bench_binary,
        "--backend",
        "openai",
        "--base-url",
        request.target_endpoint,
        "--model",
        request.model_path or request.served_model_name,
        "--served-model-name",
        request.served_model_name,
        "--dataset-name",
        "random",
        "--num-prompts",
        str(num_prompts),
        "--ignore-eos",
        "--percentile-metrics",
        "ttft,tpot,itl,e2el",
        "--save-result",
    ]
    if result_dir is not None:
        command.extend(["--result-dir", str(result_dir)])
    if result_filename:
        command.extend(["--result-filename", result_filename])
    for key, value in params.items():
        if key == "_benchmark_name":
            continue
        command.append(str(key))
        if value is not True:
            command.append(str(value))
    return command


def run_bench_case(
    request: BenchRunRequest,
    *,
    results_root: str | Path,
    work_dir: str | Path,
    bench_binary: str = "vllm-bench",
    timeout_seconds: int = 1800,
    num_prompts: int = 10,
    runner: ProcessRunner | None = None,
) -> dict[str, Any]:
    """执行一个 benchmark case 并写入 raw log/json。"""
    runner = runner or subprocess.run
    run_root = Path(results_root) / request.run_id
    raw_log_dir = run_root / "raw_logs"
    raw_json_dir = run_root / "raw_json"
    raw_log_dir.mkdir(parents=True, exist_ok=True)
    raw_json_dir.mkdir(parents=True, exist_ok=True)
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    log_path = raw_log_dir / f"{request.case_id}.log"
    json_path = raw_json_dir / f"{request.case_id}.json"

    if is_localhost_endpoint(request.target_endpoint):
        message = "target endpoint must use target Service, not localhost"
        log_path.write_text(message + "\n", encoding="utf-8")
        return {
            "success": False,
            "exit_code": None,
            "error_type": "BENCH_COMMAND_FAILED",
            "error_message": message,
            "raw_log_path": str(log_path),
            "raw_json_path": None,
            "metrics": {},
        }

    command = build_vllm_bench_command(
        request,
        bench_binary=bench_binary,
        num_prompts=num_prompts,
        result_dir=raw_json_dir,
        result_filename=f"{request.case_id}.json",
    )
    try:
        completed = runner(
            command,
            timeout=timeout_seconds,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        message = f"benchmark timed out after {timeout_seconds}s"
        log_path.write_text(message + "\n", encoding="utf-8")
        return {
            "success": False,
            "exit_code": None,
            "error_type": "BENCH_TIMEOUT",
            "error_message": message,
            "raw_log_path": str(log_path),
            "raw_json_path": None,
            "metrics": {},
        }

    stdout = getattr(completed, "stdout", "") or ""
    stderr = getattr(completed, "stderr", "") or ""
    returncode = int(getattr(completed, "returncode", 0))
    combined = stdout + ("\n" if stdout and stderr else "") + stderr
    log_path.write_text(combined, encoding="utf-8")

    saved_payload = _read_saved_result(json_path)
    metrics = parse_bench_metrics(saved_payload, combined)
    raw_payload = {
        "command": command,
        "metrics": metrics,
        "exit_code": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "vllm_bench_result": saved_payload,
    }
    json_path.write_text(json.dumps(raw_payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    if returncode != 0:
        return {
            "success": False,
            "exit_code": returncode,
            "error_type": "BENCH_COMMAND_FAILED",
            "error_message": f"vllm-bench exited with {returncode}",
            "raw_log_path": str(log_path),
            "raw_json_path": str(json_path),
            "metrics": metrics,
        }
    return {
        "success": True,
        "exit_code": 0,
        "raw_log_path": str(log_path),
        "raw_json_path": str(json_path),
        "metrics": metrics,
    }


def _read_saved_result(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    if "\n" in text:
        return json.loads(text.splitlines()[-1])
    return json.loads(text)
