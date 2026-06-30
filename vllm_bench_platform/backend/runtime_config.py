"""本地 smoke 运行参数加载。

真实平台最终会由主后端传入镜像、模型路径和厂商资源名等参数；当前最小闭环先用
`configs/enving.env` 模拟这层输入，避免把集群私有信息硬编码到源码或测试里。

维护约束：
- 这个模块处在“主后端 payload”和“本仓库 submit_run”之间，职责是适配输入，不负责
  创建 Kubernetes 资源。
- env 文件只是 smoke 闭环的本地替身，不是生产配置中心；后续接入真实后端时应保持
  `build_payload_from_files` 的 payload 形状，而不是让下游 builder 感知 env 文件。
- `NAMESPACE` 必须来自 env，是为了让本地验证能明确落在哪个 Kubernetes namespace，
  避免误把资源提交到 default。
- `MASTER_IMAGE` 和 `BENCH_RUNNER_IMAGE` 只影响 Master Job 两个控制容器；它们不能被
  传给 target Pod，否则会混淆控制面镜像和被测服务镜像。
- `TARGET_VLLM_IMAGE` 只进入 vendor profile，并最终只由 target Pod builder 消费。
- `TARGET_RESOURCE_NAME` 和 `TARGET_RESOURCE_COUNT` 代表国产卡或 GPU 资源申请；它们
  绝不能出现在 master-controller 或 bench-runner 容器资源中。
- `RESULTS_HOST_PATH` 是当前无 StorageClass 单节点 smoke 环境的折中，生产部署可以
  通过新的 change 切到 StorageClass、RWX PVC 或对象存储。
- `BENCH_COMMAND` 允许不同镜像里把轻量 vllm-bench 暴露成不同命令，但默认仍按
  `vllm bench serve` 保持和 reference 脚本一致。
- `BENCH_NUM_PROMPTS` 默认为小值，是为了 smoke 测试能尽快跑出结果；完整矩阵不应依赖
  smoke 默认值评估真实性能。
- `BENCH_TIMEOUT_SECONDS` 只约束单个 benchmark case，不代表整个 Master Job 超时策略。
- `BENCH_RUNNER_HEALTH_TIMEOUT_SECONDS` 只约束 controller 等待本 Pod 内 bench-runner agent
  的时间，本机首次拉取大镜像时需要比默认更长。
- `BENCH_RUNNER_REQUEST_TIMEOUT_SECONDS` 只约束 controller 等待 bench-runner 返回单次 case
  结果的时间，必须略大于 `BENCH_TIMEOUT_SECONDS`，否则成功 raw 结果可能写出但 summary 丢失。
- request timeout 默认用 `BENCH_TIMEOUT_SECONDS + 30`，给 agent JSON 回写和文件 flush 留余量。
- 这个默认只在 env 未显式设置时生效；本地 smoke 示例仍写明数值，便于排查 manifest。
- `MASTER_MEMORY_*` 只影响 controller 容器，不能被误传给 target vLLM。
- `BENCH_RUNNER_MEMORY_*` 只影响压测客户端，避免 vLLM CLI 导入时被低内存限制 OOMKill。
- target vLLM 的 GPU 申请完全来自 vendor_profile 的 resource_name/resource_count。
- target vLLM 的额外环境变量通过 `TARGET_ENV_JSON` 传递，当前用于关闭 HuggingFace Xet。
- `TARGET_ENV_JSON` 必须是扁平字符串字典，因为 Kubernetes env value 只能稳定表示字符串。
- `POD_TOLERATIONS_JSON` 同时给 Master Pod 和 target Pod 使用，适配单节点 control-plane taint。
- 如果后续要区分 Master 和 target toleration，应新增显式字段，不能复用现有字段改语义。
- `health_path` 和 `target_port` 进入 vendor_profile，确保 Service、health check、bench endpoint 一致。
- 本模块只做 JSON 形状校验，不校验 Kubernetes 能否接受 toleration 字段组合。
- 本模块也不校验镜像是否存在；镜像拉取失败应由 Kubernetes event 暴露。
- 本模块不读取 Docker 或 GPU 状态；硬件探测结果已经体现在 env 示例和 smoke 配置中。
- run_id 默认短随机值只服务本地 CLI，真实后端应传入可追踪的业务 run_id。
- payload 中的 model_config 保持简单，后续多模型矩阵需要新的 OpenSpec change。
- payload 中的 vendor_profile 保持单 vendor，后续多卡/多厂商 fan-out 也需要新的 change。
- 配置文件中的 `_benchmark_name` 是稳定 case id 的来源，不能在这里自动生成。
- smoke 配置故意很小；完整 reference 矩阵不能通过这个模块的默认值推断。
- `MODEL_PATH` 通常是容器内可见路径，本模块不检查宿主机路径是否存在。
- `SERVED_MODEL_NAME` 同时用于 vLLM server 和 OpenAI-compatible 请求，必须稳定传递。
- `MODEL_NAME` 允许和 served name 分离，但本地 smoke 默认复用 served name。
- `DTYPE` 保持字符串透传，不在这里枚举合法值，因为不同 vLLM 镜像支持集合可能不同。
- `HEALTH_PATH` 默认 `/health`，但保留 env 覆盖以适配厂商镜像。
- `TARGET_PORT` 默认 8000，必须和 target Service、health endpoint、bench base-url
  保持同一口径。
- `POD_TOLERATIONS_JSON` 用于单节点 kubeadm smoke，让 Master 和 target 都能调度到带
  control-plane taint 的唯一节点。
- env parser 只支持简单 `KEY=value`，故意不实现 shell 展开，避免本地文件出现隐式依赖。
- 所有缺失必填字段都在这里尽早失败，保证 submit 阶段不会半创建资源。
- reference 中 `bench_hparams.json` 的 `"--re te"` 是已知拼写问题；本模块做兼容修正，
  让参考数据能直接用于 smoke。
- 修正 reference 拼写时只在没有 `--request-rate` 的情况下生效，避免覆盖用户显式配置。
- 这里返回普通 dict payload，是为了继续复用现有 schema 校验和 builder 测试。
- 如果后续加入 HTTP API，本模块仍可作为 CLI/dev-mode 输入适配层保留。
- 不在这里生成 run_id 的强业务语义，只提供随机短 ID，生产 run_id 应由主后端分配。
- 新增 env 字段时应同步更新 `configs/enving.example.env` 和相关测试。
- 不要把密钥、token 或 registry credential 放进 env 示例；镜像拉取凭证应属于集群配置。
- 这个模块不读取 reference 目录本身，调用者显式传入 serve/bench config 路径，便于测试。
- 任何 fan-out 限制仍由 `RunConfig` 负责，本模块只组装单 namespace、单模型、单 vendor
  的 MVP payload。
- 这个文件注释较多，是为了把本地 smoke 约定留在代码旁边，降低后续接入真实后端时的误用风险。
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
    bench_runner_image: str
    target_vllm_image: str
    target_resource_name: str
    target_resource_count: int
    model_path: str
    served_model_name: str
    dtype: str
    results_host_path: str
    bench_command: str
    bench_timeout_seconds: int
    bench_num_prompts: int
    bench_runner_health_timeout_seconds: int
    bench_runner_request_timeout_seconds: int
    master_memory_request: str
    master_memory_limit: str
    bench_runner_memory_request: str
    bench_runner_memory_limit: str
    target_env: dict[str, str]
    model_name: str
    vendor_name: str
    pod_tolerations: list[dict[str, Any]]
    health_path: str = "/health"
    target_port: int = 8000


def load_env_config(path: str | Path) -> RuntimeEnvConfig:
    """读取 shell 风格的 key=value 文件并转换成强类型配置。"""
    values = _read_env_file(Path(path))
    served_model_name = _require(values, "SERVED_MODEL_NAME")
    return RuntimeEnvConfig(
        namespace=_require(values, "NAMESPACE"),
        master_image=_require(values, "MASTER_IMAGE"),
        bench_runner_image=_require(values, "BENCH_RUNNER_IMAGE"),
        target_vllm_image=_require(values, "TARGET_VLLM_IMAGE"),
        target_resource_name=_require(values, "TARGET_RESOURCE_NAME"),
        target_resource_count=int(_require(values, "TARGET_RESOURCE_COUNT")),
        model_path=_require(values, "MODEL_PATH"),
        served_model_name=served_model_name,
        dtype=_require(values, "DTYPE"),
        results_host_path=_require(values, "RESULTS_HOST_PATH"),
        bench_command=values.get("BENCH_COMMAND", "vllm bench serve"),
        bench_timeout_seconds=int(values.get("BENCH_TIMEOUT_SECONDS", "1800")),
        bench_num_prompts=int(values.get("BENCH_NUM_PROMPTS", "10")),
        bench_runner_health_timeout_seconds=int(values.get("BENCH_RUNNER_HEALTH_TIMEOUT_SECONDS", "120")),
        bench_runner_request_timeout_seconds=int(
            values.get(
                "BENCH_RUNNER_REQUEST_TIMEOUT_SECONDS",
                str(int(values.get("BENCH_TIMEOUT_SECONDS", "1800")) + 30),
            )
        ),
        master_memory_request=values.get("MASTER_MEMORY_REQUEST", "256Mi"),
        master_memory_limit=values.get("MASTER_MEMORY_LIMIT", "512Mi"),
        bench_runner_memory_request=values.get("BENCH_RUNNER_MEMORY_REQUEST", "256Mi"),
        bench_runner_memory_limit=values.get("BENCH_RUNNER_MEMORY_LIMIT", "512Mi"),
        target_env=_read_json_object(values.get("TARGET_ENV_JSON", "{}"), "TARGET_ENV_JSON"),
        model_name=values.get("MODEL_NAME", served_model_name),
        vendor_name=values.get("VENDOR_NAME", "local"),
        pod_tolerations=_read_json_list(values.get("POD_TOLERATIONS_JSON", "[]"), "POD_TOLERATIONS_JSON"),
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
            "resource_count": env.target_resource_count,
            "env": env.target_env,
            "node_selector": {},
            "tolerations": env.pod_tolerations,
            "runtime_class_name": None,
            "shm_size": "16Gi",
            "port": env.target_port,
            "health_path": env.health_path,
            "extra_serve_args": [],
        },
        "model_config": {
            "model_name": env.model_name,
            "model_path": env.model_path,
            "served_model_name": env.served_model_name,
            "trust_remote_code": True,
            "dtype": env.dtype,
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
