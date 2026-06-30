"""master-controller 最小执行循环。

维护约束：
- controller 是 Master Job 的执行核心，后端只负责创建 Job，不直接跑压测。
- 启动后必须先加载 `/configs`，再等待 bench-runner health，最后才创建 target 资源。
- serve_config 是 target 生命周期边界；每组 serve 都创建并最终删除一组 Pod/Service。
- bench_config 是请求形态边界；同一 serve 下多个 bench 复用同一个 target。
- benchmark case 失败先重试一次，第二次仍失败才写 failed_cases.jsonl。
- target 启动或 health 失败时跳过当前 serve 下所有 bench，并写失败上下文。
- 每组 serve 结束前抓取 logs/events，即使 benchmark 或 health 失败也要尽力保存。
- 清理放在 finally 中，避免中途异常留下 target Service/Pod。
- target 删除后默认 sleep 5 秒，给国产卡 runtime 释放资源留缓冲。
- best_config 必须在所有 serve/bench 完成后写入，再请求 bench-runner shutdown。
- fake client 和 fake bench client 可注入，保证双循环语义可以无集群测试。
- controller 不关心后端 CLI/env 文件，只消费已经挂载好的配置和环境变量。
- endpoint 由 Service builder 生成，避免错误使用 localhost 或 Pod IP。
- node_name 只用于 failed case 定位，获取失败时允许为空但字段仍保留。
- 当前最小闭环不实现 target 崩溃 watch；后续可在 bench 前后增加 Pod phase 检查。
- 当前 `wait_http_ready` 从 controller 容器直接访问 Service endpoint，符合 Pod 内集群 DNS 模式。
- 这个模块不捕获所有异常变成成功退出；未知异常应让 Master Job 失败，便于 kubectl 观察。
- controller 和 bench-runner 位于同一个 Pod，但 target vLLM 位于另一个 Pod。
- 因此 bench-runner health 可以用 localhost，target health 必须用 Service DNS。
- target Pod ready 只说明容器进程已启动，不代表 OpenAI API 已经可服务请求。
- target health timeout 必须和 pod ready timeout 分开记录，便于区分调度问题和模型加载问题。
- 首次 HuggingFace 下载、safetensors 加载和 torch.compile 都发生在 target health 之前。
- 同一 serve_config 下复用 target，是为了避免每个 bench_config 都重复加载模型。
- 切换 serve_config 前必须先抓日志/events，再删 Service/Pod，否则失败现场会丢失。
- failed_cases.jsonl 写入的是用户可定位的 case 粒度，而不是 Kubernetes 原始对象状态。
- 如果 target 在 ready 前变成 Failed，错误类型应标为 TARGET_POD_FAILED 而不是 PENDING。
- 如果 target 一直 Pending，通常是资源名、GPU 数量、taint/toleration 或镜像拉取问题。
- 如果 target ready 但 health 不通，通常是模型加载失败、端口不一致或 health_path 不匹配。
- 如果 bench.run_bench 抛异常，这属于控制面协议失败，应让 Job 失败暴露出来。
- 如果 bench.run_bench 返回 success=false，这属于压测 case 失败，应重试并写 failed_cases。
- best_config 只从 summary.jsonl 选择，因此任何成功 case 都必须先完整写 summary。
- shutdown 放在 best_config 之后，是为了避免 agent 提前退出导致 raw 文件还没 flush。
- release_sleep_seconds 是本机 smoke 的资源释放缓冲，不是性能测试的一部分。
- fake clients 覆盖的是双循环语义，真实 kubectl 行为由 k8s_client 单元测试和 smoke 验证覆盖。
- 新增环境变量时应从 runtime_config、Job builder、main 三处一起贯通。
- 新增 case 级错误类型时应同步更新 schemas、writer 测试和分析器兼容逻辑。
- 如果 cleanup force delete 失败，应在后续任务中补充 TARGET_POD_FORCE_DELETED 记录。
- `main` 只读取 RUN_ID/NAMESPACE/目录环境变量，与 Job builder 的 env 保持一致。
- 新增执行步骤时应优先在 `tests/test_master_controller_loop.py` 增加 fake-client 场景。
"""

from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import time
from typing import Any

from vllm_bench_platform.master.analyzer import write_best_config
from vllm_bench_platform.master.bench_client import BenchRunnerClient
from vllm_bench_platform.master.k8s_client import KubectlMasterClient
from vllm_bench_platform.master.matrix_loader import load_run_config_from_dir
from vllm_bench_platform.master.result_writer import ResultWriter
from vllm_bench_platform.master.service_builder import build_target_service, target_endpoint
from vllm_bench_platform.master.target_pod_builder import build_target_pod, target_pod_name
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
    bench_health_timeout_seconds: int = 120,
    bench_request_timeout_seconds: int = 30,
) -> None:
    """执行 serve_config x bench_config 的最小可运行闭环。"""
    run_config = load_run_config_from_dir(config_dir, run_id=run_id, namespace=namespace)
    k8s = k8s_client or KubectlMasterClient()
    bench = bench_client or BenchRunnerClient(request_timeout_seconds=bench_request_timeout_seconds)
    writer = ResultWriter(results_root, run_id)
    writer.initialize({"namespace": namespace, "config_dir": str(config_dir)})

    if not bench.wait_health(timeout_seconds=bench_health_timeout_seconds):
        raise RuntimeError("bench-runner health timeout")

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
    bench.shutdown()


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
            error_type = ErrorType.TARGET_POD_FAILED if phase == "Failed" else ErrorType.TARGET_POD_PENDING
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
        bench_health_timeout_seconds=int(os.environ.get("BENCH_RUNNER_HEALTH_TIMEOUT_SECONDS", "120")),
        bench_request_timeout_seconds=int(os.environ.get("BENCH_RUNNER_REQUEST_TIMEOUT_SECONDS", "30")),
    )


if __name__ == "__main__":
    main()
