"""target Service manifest 构造。

维护约束：
- master-controller 内置压测执行器必须通过 Service 名称访问 target，不能使用 localhost 或 Pod IP。
- Service 名称包含 run_id 和 serve benchmark name，方便日志和事件定位。
- selector 必须与 target Pod labels 完全一致，否则 health 和 bench 都会打到空 endpoints。
- Service port 使用 vendor_profile.port，避免在多个 builder 中重复端口配置。
- 名称中下划线替换为短横线，是为了更贴近 Kubernetes DNS 名称要求。
- 这里不创建 Headless Service，MVP 只需要一个稳定 ClusterIP 入口。
- 如果后续支持多副本 target，selector 语义可以复用，但 health 策略需要重新设计。
"""

from __future__ import annotations

from typing import Any

from vllm_bench_platform.schemas import RunConfig, ServeConfig


def target_service_name(run_config: RunConfig, serve_config: ServeConfig) -> str:
    """生成包含 run 和 serve 身份的稳定 Service 名称。"""
    return f"vllm-target-{run_config.run_id}-{serve_config.benchmark_name}".replace("_", "-")


def build_target_service(run_config: RunConfig, serve_config: ServeConfig) -> dict[str, Any]:
    """为 target Pod 暴露 OpenAI-compatible endpoint。"""
    name = target_service_name(run_config, serve_config)
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "namespace": run_config.namespace,
            "labels": {
                "app": "vllm-bench-target",
                "run_id": run_config.run_id,
                "serve_config": serve_config.benchmark_name,
            },
        },
        "spec": {
            "selector": {
                "app": "vllm-bench-target",
                "run_id": run_config.run_id,
                "serve_config": serve_config.benchmark_name,
            },
            "ports": [
                {
                    "name": "http",
                    "port": run_config.vendor_profile.port,
                    "targetPort": run_config.vendor_profile.port,
                }
            ],
        },
    }


def target_endpoint(run_config: RunConfig, serve_config: ServeConfig) -> str:
    """构造传给 vllm-bench 的 Service endpoint。"""
    return f"http://{target_service_name(run_config, serve_config)}:{run_config.vendor_profile.port}"
