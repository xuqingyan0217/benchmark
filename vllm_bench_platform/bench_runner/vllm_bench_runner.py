"""bench-runner 的核心压测执行逻辑。

HTTP agent 和 master-controller 都围绕这里的纯函数协作：构造命令、拒绝错误 endpoint、
持久化 raw 输出、返回结构化结果。这样单元测试无需启动真实 HTTP 服务或 vLLM 进程。

维护约束：
- bench-runner 只访问 target Service endpoint，绝不访问 target Pod IP 或 localhost。
- localhost 检查放在执行命令前，是为了防止误把 bench-runner 自己当成 vLLM 服务压测。
- 命令构造保留用户提交的 bench CLI flag，平台不主动改写 vllm-bench 参数语义。
- `--re te` 兼容只为 reference 数据服务，真实输入仍应使用 `--request-rate`。
- raw log 总是写入，即使 endpoint 非法、命令失败或 timeout，也要给运维人员留下证据。
- raw json 在命令执行后写入，保存命令、stdout、stderr、exit code 和解析出的 metrics。
- subprocess runner 可注入，测试不需要安装 vLLM 或访问真实 target。
- `timeout_seconds` 是单 case 上限；master-controller 仍负责失败重试和 failed case 归档。
- `num_prompts` 由 Master Job 环境变量传入，smoke 默认小值，完整压测可提高。
- `model_path` 用于 tokenizer/数据构造，`served_model_name` 用于 OpenAI-compatible 请求。
- 成功标准只看进程 exit code；指标缺失不强行判失败，因为 raw 输出可能仍有排障价值。
- 失败类型使用现有 MVP 枚举字符串，避免 bench-runner 引入额外 schema 依赖循环。
- work_dir 只保存临时执行上下文，不作为最终 artifact 返回。
- 所有返回路径都是字符串，便于 HTTP JSON 序列化和 summary 写入。
- 这里不做 prefix cache reset；不同 target 镜像是否支持该 endpoint 属于后续增强。
- 如果后续改用 vllm-bench JSON 输出，应保持 `run_bench_case` 返回结构不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shlex
import subprocess
from typing import Any, Callable
from urllib.parse import urlparse

from vllm_bench_platform.backend.runtime_config import normalize_bench_params
from vllm_bench_platform.bench_runner.result_parser import parse_vllm_bench_output


ProcessRunner = Callable[..., Any]


@dataclass(frozen=True)
class BenchRunRequest:
    """master-controller 调用 `/run-bench` 的最小请求结构。"""

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


def is_localhost_endpoint(endpoint: str) -> bool:
    """判断 endpoint 是否错误指向 bench-runner 自己。"""
    host = urlparse(endpoint).hostname
    return host in {"localhost", "127.0.0.1", "::1"}


def build_vllm_bench_command(
    request: BenchRunRequest,
    *,
    bench_command: str = "vllm bench serve",
    num_prompts: int = 10,
) -> list[str]:
    """构造 vllm-bench 命令，保留用户提交的 bench CLI flag。"""
    params = normalize_bench_params(request.bench_params)
    command = shlex.split(bench_command)
    command.extend(
        [
            "--endpoint-type",
            "openai-comp",
            "--base-url",
            request.target_endpoint,
            "--model",
            request.model_path,
            "--served-model-name",
            request.served_model_name,
            "--dataset-name",
            "random",
            "--num-prompts",
            str(num_prompts),
            "--ignore-eos",
            "--percentile-metrics",
            "ttft,tpot,itl,e2el",
        ]
    )
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
    bench_command: str = "vllm bench serve",
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
        bench_command=bench_command,
        num_prompts=num_prompts,
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
    metrics = parse_vllm_bench_output(combined)
    payload = {
        "command": command,
        "metrics": metrics,
        "exit_code": returncode,
        "stdout": stdout,
        "stderr": stderr,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    if returncode != 0:
        return {
            "success": False,
            "exit_code": returncode,
            "error_type": "BENCH_COMMAND_FAILED",
            "error_message": f"vllm bench exited with {returncode}",
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


def request_from_payload(payload: dict[str, Any]) -> BenchRunRequest:
    """把 HTTP JSON payload 转为 BenchRunRequest。"""
    return BenchRunRequest(
        target_endpoint=payload["target_endpoint"],
        run_id=payload["run_id"],
        serve_benchmark_name=payload["serve_benchmark_name"],
        bench_benchmark_name=payload["bench_benchmark_name"],
        bench_params=dict(payload.get("bench_params", {})),
        model_path=payload.get("model_path", ""),
        served_model_name=payload.get("served_model_name", ""),
    )
