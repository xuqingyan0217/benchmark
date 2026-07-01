"""master-controller 最小执行循环。

Master Job 现在是单容器：controller 负责 target Pod/Service 生命周期，也直接调用
容器内的 `vllm-bench` 二进制执行 benchmark case。
"""

from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import time
from typing import Any

from vllm_bench_platform.master.analyzer import write_best_config
from vllm_bench_platform.master.bench_runner import DirectBenchRunner
from vllm_bench_platform.master.k8s_client import KubectlMasterClient
from vllm_bench_platform.master.matrix_loader import load_run_config_from_dir
from vllm_bench_platform.master.result_writer import ResultWriter
from vllm_bench_platform.master.service_builder import build_target_service, target_endpoint
from vllm_bench_platform.master.target_pod_builder import build_target_pod, target_pod_name
from vllm_bench_platform.resource_planner import apply_resource_plan, plan_model_resources
from vllm_bench_platform.schemas import BenchConfig, ErrorType, RunConfig, ServeConfig


def run_controller(
    *,
    config_dir: str | Path,
    results_root: str | Path,
    work_dir: str | Path,
    run_id: str,
    namespace: str,
    k8s_client: Any | None = None,
    bench_client: Any | None = None,
    release_sleep_seconds: int = 5,
    bench_binary: str = "vllm-bench",
    bench_timeout_seconds: int = 1800,
    bench_num_prompts: int = 10,
    target_gpu_memory_gb: float = 0.0,
    hf_endpoint: str = "https://huggingface.co",
    hf_token: str | None = None,
    resource_metadata_fetcher: Any | None = None,
) -> None:
    """执行 serve_config x bench_config 的最小可运行闭环。"""
    run_config = load_run_config_from_dir(config_dir, run_id=run_id, namespace=namespace)
    if target_gpu_memory_gb <= 0:
        raise ValueError("TARGET_GPU_MEMORY_GB must be greater than 0")
    run_config = apply_resource_plan(
        run_config,
        plan_model_resources(
            memory_per_gpu_gb=target_gpu_memory_gb,
            model_id=run_config.model_config.model_path,
            fallback_model_id=run_config.model_config.model_name,
            hf_endpoint=hf_endpoint,
            hf_token=hf_token,
            fetch_json=resource_metadata_fetcher,
        ),
    )
    k8s = k8s_client or KubectlMasterClient()
    bench = bench_client or DirectBenchRunner(
        results_root=results_root,
        work_dir=work_dir,
        bench_binary=bench_binary,
        timeout_seconds=bench_timeout_seconds,
        num_prompts=bench_num_prompts,
    )
    writer = ResultWriter(results_root, run_id)
    writer.initialize({"namespace": namespace, "config_dir": str(config_dir)})

    for serve_config in run_config.serve_configs:
        _run_serve_group(
            run_config,
            serve_config,
            k8s,
            bench,
            writer,
        )
        if release_sleep_seconds:
            time.sleep(release_sleep_seconds)
    write_best_config(writer.run_root)


def _run_serve_group(
    run_config: RunConfig,
    serve_config: ServeConfig,
    k8s: Any,
    bench: Any,
    writer: ResultWriter,
) -> None:
    pod = build_target_pod(run_config, serve_config)
    service = build_target_service(run_config, serve_config)
    pod_name = pod["metadata"]["name"]
    service_name = service["metadata"]["name"]
    endpoint = target_endpoint(run_config, serve_config)
    node_name = ""
    try:
        k8s.create_pod(pod)
        k8s.create_service(service)
        node_name = k8s.pod_node_name(pod_name, run_config.namespace)
        if not k8s.wait_pod_ready(pod_name, run_config.namespace):
            phase = _pod_phase(k8s, pod_name, run_config.namespace)
            failure_reason = _pod_failure_reason(k8s, pod_name, run_config.namespace)
            error_type = ErrorType.TARGET_POD_FAILED if phase == "Failed" or failure_reason else ErrorType.TARGET_POD_PENDING
            if failure_reason:
                message = f"target pod failed before ready: {failure_reason}"
            else:
                message = "target pod failed before ready" if phase == "Failed" else "target pod ready timeout"
            _write_failed_for_all_benches(run_config, serve_config, writer, pod_name, node_name, error_type, message)
            return
        if not k8s.wait_http_ready(endpoint + run_config.vendor_profile.health_path):
            _write_failed_for_all_benches(run_config, serve_config, writer, pod_name, node_name, ErrorType.TARGET_HEALTH_TIMEOUT, "target health timeout")
            return
        for bench_config in run_config.bench_configs:
            _run_bench_with_retry(
                run_config,
                serve_config,
                bench_config,
                endpoint,
                pod_name,
                node_name,
                bench,
                writer,
            )
    finally:
        writer.write_server_log(serve_config.benchmark_name, k8s.get_pod_logs(pod_name, run_config.namespace))
        writer.write_events(serve_config.benchmark_name, k8s.get_pod_events(pod_name, run_config.namespace))
        k8s.delete_service(service_name, run_config.namespace)
        k8s.delete_pod(pod_name, run_config.namespace)
        k8s.wait_pod_deleted(pod_name, run_config.namespace)


def _run_bench_with_retry(
    run_config: RunConfig,
    serve_config: ServeConfig,
    bench_config: BenchConfig,
    endpoint: str,
    pod_name: str,
    node_name: str,
    bench: Any,
    writer: ResultWriter,
) -> None:
    last_result: dict[str, Any] | None = None
    for attempt in (1, 2):
        case_id = f"{serve_config.benchmark_name}-{bench_config.benchmark_name}"
        result = bench.run_bench(
            {
                "target_endpoint": endpoint,
                "run_id": run_config.run_id,
                "case_id": case_id,
                "serve_benchmark_name": serve_config.benchmark_name,
                "bench_benchmark_name": bench_config.benchmark_name,
                "bench_params": bench_config.as_cli_args(),
                "model_path": run_config.model_config.model_path,
                "served_model_name": run_config.model_config.served_model_name,
            }
        )
        last_result = result
        if result.get("success"):
            writer.append_summary(
                {
                    "run_id": run_config.run_id,
                    "case_id": case_id,
                    "serve_config": serve_config.benchmark_name,
                    "bench_config": bench_config.benchmark_name,
                    "target_endpoint": endpoint,
                    "attempt": attempt,
                    "raw_json_path": result.get("raw_json_path", ""),
                    "raw_log_path": result.get("raw_log_path", ""),
                    "metrics": result.get("metrics", {}),
                }
            )
            return
    assert last_result is not None
    writer.append_failed_case(
        _failed_case_record(
            run_config,
            serve_config,
            bench_config,
            2,
            pod_name,
            node_name,
            last_result.get("error_type", ErrorType.BENCH_COMMAND_FAILED.value),
            last_result.get("error_message", "benchmark failed"),
            last_result.get("raw_log_path", ""),
        )
    )


def _write_failed_for_all_benches(
    run_config: RunConfig,
    serve_config: ServeConfig,
    writer: ResultWriter,
    pod_name: str,
    node_name: str,
    error_type: ErrorType,
    message: str,
) -> None:
    for bench_config in run_config.bench_configs:
        writer.append_failed_case(
            _failed_case_record(
                run_config,
                serve_config,
                bench_config,
                1,
                pod_name,
                node_name,
                error_type.value,
                message,
                "",
            )
        )


def _pod_phase(k8s: Any, pod_name: str, namespace: str) -> str:
    try:
        return k8s.pod_phase(pod_name, namespace)
    except Exception:
        return ""


def _pod_failure_reason(k8s: Any, pod_name: str, namespace: str) -> str:
    try:
        return k8s.pod_failure_reason(pod_name, namespace)
    except Exception:
        return ""


def _failed_case_record(
    run_config: RunConfig,
    serve_config: ServeConfig,
    bench_config: BenchConfig,
    attempt: int,
    pod_name: str,
    node_name: str,
    error_type: str,
    message: str,
    raw_log_path: str,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "run_id": run_config.run_id,
        "case_id": f"{serve_config.benchmark_name}-{bench_config.benchmark_name}",
        "serve_config": serve_config.as_cli_args(),
        "bench_config": bench_config.as_cli_args(),
        "attempt": attempt,
        "error_type": error_type,
        "error_message": message,
        "raw_log_path": raw_log_path,
        "target_pod_name": pod_name,
        "target_node_name": node_name,
        "start_time": now,
        "end_time": now,
    }


def main() -> None:
    run_controller(
        config_dir=os.environ.get("CONFIG_DIR", "/configs"),
        results_root=os.environ.get("RESULTS_ROOT", "/results"),
        work_dir=os.environ.get("WORK_DIR", "/work"),
        run_id=os.environ["RUN_ID"],
        namespace=os.environ["NAMESPACE"],
        bench_binary=os.environ.get("BENCH_BINARY", "vllm-bench"),
        bench_timeout_seconds=int(os.environ.get("BENCH_TIMEOUT_SECONDS", "1800")),
        bench_num_prompts=int(os.environ.get("BENCH_NUM_PROMPTS", "10")),
        target_gpu_memory_gb=float(os.environ["TARGET_GPU_MEMORY_GB"]),
        hf_endpoint=os.environ.get("HF_ENDPOINT", "https://huggingface.co"),
        hf_token=os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"),
    )


if __name__ == "__main__":
    main()
