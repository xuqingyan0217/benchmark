"""target vLLM Pod manifest 构造。

维护约束：
- 只有这个 builder 可以把 vendor resource_name/resource_count 写入容器资源申请。
- Master Job builder 绝不能复制这里的 accelerator 逻辑。
- target Pod 运行被测 vLLM server，不直接写 `/results`，结果统一由 Master Pod 管理。
- serve_config.params 保留 CLI flag 形式，按原样追加到 vLLM server 命令。
- model_config 描述模型加载，不能混入 bench 请求参数。
- vendor env、node selector、tolerations、runtime class、shm size 都集中从 vendor_profile 读取。
- `/dev/shm` 使用 emptyDir memory，是为了适配 vLLM 常见 shared-memory 需求。
- target Pod restartPolicy 为 Never，失败由 controller 记录和推进下一组 serve_config。
- 这里不创建 Service，Service 选择器和访问入口由 service_builder 单独维护。
- 镜像拉取凭证不在 MVP builder 内硬编码，集群应通过 imagePullSecrets 或节点配置解决。
- 如果厂商镜像不使用 `python3 -m vllm.entrypoints.openai.api_server`，后续应通过
  vendor_profile 扩展命令字段，而不是在这里写厂商分支。
"""

from __future__ import annotations

from typing import Any

from vllm_bench_platform.schemas import RunConfig, ServeConfig


def target_pod_name(run_config: RunConfig, serve_config: ServeConfig) -> str:
    """生成 target Pod 名称。"""
    return f"vllm-target-{run_config.run_id}-{serve_config.benchmark_name}".replace("_", "-")


def build_target_pod(run_config: RunConfig, serve_config: ServeConfig) -> dict[str, Any]:
    """构造只由 target container 申请 accelerator 的 Pod。"""
    vendor = run_config.vendor_profile
    model = run_config.model_config
    labels = {
        "app": "vllm-bench-target",
        "run_id": run_config.run_id,
        "serve_config": serve_config.benchmark_name,
    }
    args = [
        "--host",
        "0.0.0.0",
        "--port",
        str(vendor.port),
        "--model",
        model.model_path,
        "--served-model-name",
        model.served_model_name,
        "--dtype",
        model.dtype,
        "--tensor-parallel-size",
        str(vendor.tensor_parallel_size),
        "--pipeline-parallel-size",
        str(vendor.pipeline_parallel_size),
    ]
    if model.trust_remote_code:
        args.append("--trust-remote-code")
    if model.tokenizer_path:
        args.extend(["--tokenizer", model.tokenizer_path])
    for key, value in serve_config.params.items():
        args.append(str(key))
        if value is not True:
            args.append(str(value))
    args.extend(vendor.extra_serve_args)
    volume_mounts: list[dict[str, Any]] = [
        {
            "name": "dshm",
            "mountPath": "/dev/shm",
        }
    ]
    volumes: list[dict[str, Any]] = [
        {
            "name": "dshm",
            "emptyDir": {
                "medium": "Memory",
                "sizeLimit": vendor.shm_size,
            },
        }
    ]
    env = [{"name": name, "value": value} for name, value in vendor.env.items()]
    if model.model_host_path and model.model_mount_path:
        volumes.append(
            {
                "name": "model",
                "hostPath": {
                    "path": model.model_host_path,
                    "type": "Directory",
                },
            }
        )
        volume_mounts.append(
            {
                "name": "model",
                "mountPath": model.model_mount_path,
                "readOnly": True,
            }
        )
    if model.model_cache_host_path:
        volumes.append(
            {
                "name": "model-cache",
                "hostPath": {
                    "path": model.model_cache_host_path,
                    "type": "DirectoryOrCreate",
                },
            }
        )
        volume_mounts.append(
            {
                "name": "model-cache",
                "mountPath": model.model_cache_mount_path,
            }
        )
        env.extend(
            [
                {"name": "HF_HOME", "value": model.model_cache_mount_path},
                {"name": "HUGGINGFACE_HUB_CACHE", "value": model.model_cache_mount_path},
            ]
        )
    pod_spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "containers": [
            {
                "name": "target-vllm",
                "image": vendor.target_vllm_image,
                "command": ["python3", "-m", "vllm.entrypoints.openai.api_server"],
                "args": args,
                "ports": [
                    {
                        "containerPort": vendor.port,
                        "name": "http",
                    }
                ],
                "env": env,
                "resources": {
                    "requests": {
                        vendor.resource_name: vendor.resource_count,
                    },
                    "limits": {
                        vendor.resource_name: vendor.resource_count,
                    },
                },
                "volumeMounts": volume_mounts,
            }
        ],
        "volumes": volumes,
    }
    if vendor.node_selector:
        pod_spec["nodeSelector"] = vendor.node_selector
    if vendor.tolerations:
        pod_spec["tolerations"] = vendor.tolerations
    if vendor.runtime_class_name:
        pod_spec["runtimeClassName"] = vendor.runtime_class_name
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": target_pod_name(run_config, serve_config),
            "namespace": run_config.namespace,
            "labels": labels,
        },
        "spec": pod_spec,
    }
