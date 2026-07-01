"""本地 smoke 运行参数加载。

真实平台最终会由主后端传入镜像、模型路径和厂商资源名等参数；当前闭环先用
`configs/enving.env` 模拟这层输入，避免把集群私有信息硬编码到源码或测试里。

维护约束：
- 这个模块处在“主后端 payload”和“本仓库 submit_run”之间，职责是适配输入，不负责创建 Kubernetes 资源。
- env 文件只是 smoke 闭环的本地替身，不是生产配置中心。
- `NAMESPACE` 必须来自 env，避免误把资源提交到 default。
- `MASTER_IMAGE` 只影响 Master Job 单个控制容器，不能传给 target Pod。
- `TARGET_VLLM_IMAGE` 只进入 vendor profile，并最终只由 target Pod builder 消费。
- `TARGET_RESOURCE_NAME` 和 `TARGET_RESOURCE_COUNT` 代表国产卡或 GPU 资源申请，绝不能出现在 Master 容器资源中。
- `PERSIST_ROOT` 是当前无 StorageClass 单节点 smoke 环境的持久化根目录。
- `BENCH_BINARY` 表示 Master 容器内的 vllm-bench 可执行文件名或路径。
- `BENCH_NUM_PROMPTS` 默认为小值，是为了 smoke 测试能尽快跑出结果。
- `BENCH_TIMEOUT_SECONDS` 只约束单个 benchmark case，不代表整个 Master Job 超时策略。
- `MASTER_MEMORY_*` 只影响 Master 容器，不能被误传给 target vLLM。
- target vLLM 的 GPU 申请完全来自 vendor_profile 的 resource_name/resource_count。
- target vLLM 的额外环境变量通过 `TARGET_ENV_JSON` 传递。
- `POD_TOLERATIONS_JSON` 同时给 Master Pod 和 target Pod 使用，适配单节点 control-plane taint。
- `health_path` 和 `target_port` 进入 vendor_profile，确保 Service、health check、bench base-url 一致。
- 本模块不校验镜像是否存在；镜像拉取失败应由 Kubernetes event 暴露。
- `MODEL_PATH` 通常是容器内可见路径，本模块不检查宿主机路径是否存在。
- `SERVED_MODEL_NAME` 同时用于 vLLM server 和 OpenAI-compatible 请求，必须稳定传递。
- reference 中 `bench_hparams.json` 的 `"--re te"` 是已知拼写问题；本模块做兼容修正。
- 新增 env 字段时应同步更新 `configs/enving.example.env` 和相关测试。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

@dataclass(frozen=True)
class RuntimeEnvConfig:
    """一次 smoke 提交所需的外部运行参数。"""

    namespace: str
    master_image: str
    target_vllm_image: str
    target_resource_name: str
    target_gpu_memory_gb: float
    model_path: str
    served_model_name: str
    dtype: str
    persist_root: str
    bench_binary: str
    bench_timeout_seconds: int
    bench_num_prompts: int
    master_memory_request: str
    master_memory_limit: str
    target_env: dict[str, str]
    model_name: str
    vendor_name: str
    pod_tolerations: list[dict[str, Any]]
    model_host_path: str | None = None
    model_mount_path: str | None = None
    model_cache_host_path: str | None = None
    model_cache_mount_path: str = "/root/.cache/huggingface"
    hf_endpoint: str = "https://huggingface.co"
    hf_token: str = ""
    health_path: str = "/health"
    target_port: int = 8000


def load_env_config(path: str | Path) -> RuntimeEnvConfig:
    """读取 shell 风格的 key=value 文件并转换成强类型配置。"""
    values = _read_env_file(Path(path))
    served_model_name = _require(values, "SERVED_MODEL_NAME")
    return RuntimeEnvConfig(
        namespace=_require(values, "NAMESPACE"),
        master_image=_require(values, "MASTER_IMAGE"),
        target_vllm_image=_require(values, "TARGET_VLLM_IMAGE"),
        target_resource_name=_require(values, "TARGET_RESOURCE_NAME"),
        target_gpu_memory_gb=float(_require(values, "TARGET_GPU_MEMORY_GB")),
        model_path=_require(values, "MODEL_PATH"),
        served_model_name=served_model_name,
        dtype=_require(values, "DTYPE"),
        persist_root=_require(values, "PERSIST_ROOT"),
        bench_binary=values.get("BENCH_BINARY", "vllm-bench"),
        bench_timeout_seconds=int(values.get("BENCH_TIMEOUT_SECONDS", "1800")),
        bench_num_prompts=int(values.get("BENCH_NUM_PROMPTS", "10")),
        master_memory_request=values.get("MASTER_MEMORY_REQUEST", "256Mi"),
        master_memory_limit=values.get("MASTER_MEMORY_LIMIT", "512Mi"),
        target_env=_read_json_object(values.get("TARGET_ENV_JSON", "{}"), "TARGET_ENV_JSON"),
        model_name=values.get("MODEL_NAME", served_model_name),
        vendor_name=values.get("VENDOR_NAME", "local"),
        pod_tolerations=_read_json_list(values.get("POD_TOLERATIONS_JSON", "[]"), "POD_TOLERATIONS_JSON"),
        model_host_path=_optional_value(values, "MODEL_HOST_PATH"),
        model_mount_path=_optional_value(values, "MODEL_MOUNT_PATH"),
        model_cache_host_path=_optional_value(values, "MODEL_CACHE_HOST_PATH"),
        model_cache_mount_path=values.get("MODEL_CACHE_MOUNT_PATH", "/root/.cache/huggingface"),
        hf_endpoint=values.get("HF_ENDPOINT", "https://huggingface.co"),
        hf_token=values.get("HF_TOKEN", values.get("HUGGING_FACE_HUB_TOKEN", "")),
        health_path=values.get("HEALTH_PATH", "/health"),
        target_port=int(values.get("TARGET_PORT", "8000")),
    )


def build_payload_from_files(
    env: RuntimeEnvConfig,
    serve_configs_path: str | Path,
    bench_configs_path: str | Path,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """从 env 和两个矩阵文件生成 submit_run 可消费的 payload。"""
    serve_hparams = _load_matrix(Path(serve_configs_path))
    bench_hparams = [_normalize_bench_params(item) for item in _load_matrix(Path(bench_configs_path))]
    return {
        "run_id": run_id or f"run-{uuid4().hex[:8]}",
        "namespace": env.namespace,
        "serve_hparams": serve_hparams,
        "bench_hparams": bench_hparams,
        "vendor_profile": {
            "vendor_name": env.vendor_name,
            "target_vllm_image": env.target_vllm_image,
            "resource_name": env.target_resource_name,
            "resource_count": 1,
            "env": env.target_env,
            "node_selector": {},
            "tolerations": env.pod_tolerations,
            "runtime_class_name": None,
            "shm_size": "16Gi",
            "port": env.target_port,
            "health_path": env.health_path,
            "extra_serve_args": [],
            "tensor_parallel_size": 1,
            "pipeline_parallel_size": 1,
        },
        "model_config": {
            "model_name": env.model_name,
            "model_path": env.model_path,
            "served_model_name": env.served_model_name,
            "trust_remote_code": True,
            "dtype": env.dtype,
            "model_host_path": env.model_host_path,
            "model_mount_path": env.model_mount_path,
            "model_cache_host_path": env.model_cache_host_path,
            "model_cache_mount_path": env.model_cache_mount_path,
        },
    }


def normalize_bench_params(params: dict[str, Any]) -> dict[str, Any]:
    """规范化 reference 中的已知参数拼写问题。"""
    return _normalize_bench_params(params)


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _load_matrix(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} 必须是 JSON array")
    return [dict(item) for item in data]


def _normalize_bench_params(params: dict[str, Any]) -> dict[str, Any]:
    result = dict(params)
    if "--re te" in result and "--request-rate" not in result:
        result["--request-rate"] = result.pop("--re te")
    return result


def _read_json_list(raw_value: str, name: str) -> list[dict[str, Any]]:
    data = json.loads(raw_value)
    if not isinstance(data, list):
        raise ValueError(f"{name} 必须是 JSON array")
    return [dict(item) for item in data]


def _read_json_object(raw_value: str, name: str) -> dict[str, str]:
    data = json.loads(raw_value)
    if not isinstance(data, dict):
        raise ValueError(f"{name} 必须是 JSON object")
    return {str(key): str(value) for key, value in data.items()}


def _require(values: dict[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} 不能为空")
    return value


def _optional_value(values: dict[str, str], name: str) -> str | None:
    value = values.get(name, "").strip()
    return value or None
