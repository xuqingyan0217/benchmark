"""平台共享数据结构与校验规则。

这些 schema 是 backend、master-controller、bench-runner 之间的共同契约。
MVP 阶段优先使用标准库 dataclass，避免在早期把实现绑定到某个 Web 框架或校验库。

这里的校验只覆盖 OpenSpec 已确认的 MVP 规则：
单集群、单 namespace、单模型、单 vendor profile，以及矩阵项必须有 `_benchmark_name`。

维护约束：
- schema 层只表达“数据是否满足平台契约”，不创建 Kubernetes 资源。
- backend builder 负责把这些结构翻译成 ConfigMap、PVC、Job 等 manifest。
- master-controller 后续负责把这些结构展开成 serve_config x bench_config 矩阵。
- bench-runner 后续只消费 bench case，不应该反向修改 run 级配置。
- `_benchmark_name` 是贯穿结果归档、Service 命名和失败记录的身份字段。
- serve_config 变化代表服务端参数变化，必须重建 target vLLM Pod。
- bench_config 变化代表请求形态变化，不应该重启 target vLLM Pod。
- vendor profile 集中收纳国产卡差异，避免把厂商字段散落在多个 builder。
- model config 只描述模型加载，不描述 benchmark 请求形态。
- failed case schema 是排障入口，所以定位字段必须稳定且完整。
- ErrorType 必须是受控集合，避免日志里出现拼写各异的失败原因。
- MVP fan-out 限制放在 RunConfig，是为了阻止后端创建当前执行器无法消费的任务。
- 后续扩展多集群、多模型、多 vendor 时，应通过新的 OpenSpec change 修改这里。
- 这里不做路径存在性检查，因为模型路径、日志路径、PVC 路径通常在集群内才可见。
- 这里不做 Kubernetes 资源名合法化，后续命名策略应由对应 builder 明确处理。
- 这里保留 params 中的原始 CLI flag key，是为了保证用户提交参数不被 schema 层误改。
- dataclass 使用 frozen=True，避免配置对象在执行过程中被多个组件隐式修改。
- 所有错误信息优先包含字段名，便于 API 层向用户返回明确的配置问题。
- 本模块注释较多，是为了把 OpenSpec 中的领域约束留在代码旁边，便于后续维护。
- 如果后续引入 Pydantic 或 FastAPI model，应保持这些语义约束不变。
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ValidationError(ValueError):
    """配置或结果结构不满足 MVP 契约。"""


def _require_text(name: str, value: str | None) -> str:
    """统一处理必填字符串，保证错误信息能指向字段名。"""
    if value is None or not str(value).strip():
        raise ValidationError(f"{name} 不能为空")
    return str(value)


@dataclass(frozen=True)
class MatrixConfig:
    """serve/bench 参数矩阵的公共基类。

    `_benchmark_name` 是结果归档和 target Service 命名的身份字段；
    其他参数保持原始 CLI flag 形态，后续才能无损传给 vLLM 或 vllm-bench。
    """

    _benchmark_name: str
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # dataclass frozen 后仍需要规范化字段，所以使用 object.__setattr__。
        name = _require_text("_benchmark_name", self._benchmark_name)
        object.__setattr__(self, "_benchmark_name", name)
        object.__setattr__(self, "params", dict(self.params))

    @property
    def benchmark_name(self) -> str:
        """提供更符合 Python 调用习惯的只读别名。"""
        return self._benchmark_name

    def as_cli_args(self) -> dict[str, Any]:
        """恢复成配置文件中的 CLI 参数 map。"""
        return {"_benchmark_name": self._benchmark_name, **self.params}


class ServeConfig(MatrixConfig):
    """vLLM 服务端参数；变化时必须重建 target Pod。"""

    pass


class BenchConfig(MatrixConfig):
    """vllm-bench 请求参数；变化时复用当前 target Pod。"""

    pass


@dataclass(frozen=True)
class VendorProfile:
    """国产卡 vendor profile。

    该结构集中承载厂商差异：镜像、资源名、调度字段、runtime class 和 health path。
    只有 target vLLM Pod 会使用其中的 accelerator resource。
    """

    vendor_name: str
    target_vllm_image: str
    resource_name: str
    resource_count: int
    env: dict[str, str] = field(default_factory=dict)
    node_selector: dict[str, str] = field(default_factory=dict)
    tolerations: list[dict[str, Any]] = field(default_factory=list)
    runtime_class_name: str | None = None
    shm_size: str = "16Gi"
    port: int = 8000
    health_path: str = "/health"
    extra_serve_args: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # resource_count 必须为正，避免生成无法调度或语义不明确的 target Pod。
        _require_text("vendor_name", self.vendor_name)
        _require_text("target_vllm_image", self.target_vllm_image)
        _require_text("resource_name", self.resource_name)
        if self.resource_count <= 0:
            raise ValidationError("resource_count 必须大于 0")
        if not 1 <= self.port <= 65535:
            raise ValidationError("port 必须在 1 到 65535 之间")
        _require_text("health_path", self.health_path)


@dataclass(frozen=True)
class ModelConfig:
    """模型加载配置。"""

    model_name: str
    model_path: str
    served_model_name: str
    trust_remote_code: bool
    dtype: str
    tokenizer_path: str | None = None

    def __post_init__(self) -> None:
        # tokenizer_path 可选，其余字段是启动 vLLM server 的最低输入。
        _require_text("model_name", self.model_name)
        _require_text("model_path", self.model_path)
        _require_text("served_model_name", self.served_model_name)
        _require_text("dtype", self.dtype)


@dataclass(frozen=True)
class RunConfig:
    """一次 MVP run 的完整配置。

    MVP 只允许一个集群、一个 namespace、一个模型和一个 vendor profile；
    serve_configs x bench_configs 的矩阵在 master-controller 中展开。
    """

    run_id: str
    namespace: str
    serve_configs: list[ServeConfig]
    bench_configs: list[BenchConfig]
    vendor_profile: VendorProfile
    model_config: ModelConfig
    clusters: list[str] = field(default_factory=lambda: ["default"])
    vendor_profiles: list[VendorProfile] | None = None
    model_configs: list[ModelConfig] | None = None

    def __post_init__(self) -> None:
        # 这里先拦住 MVP 外的 fan-out，避免后端创建出无法被当前 Master Job 消费的资源。
        _require_text("run_id", self.run_id)
        _require_text("namespace", self.namespace)
        if not self.serve_configs:
            raise ValidationError("serve_configs 不能为空")
        if not self.bench_configs:
            raise ValidationError("bench_configs 不能为空")
        if len(self.clusters) != 1:
            raise ValidationError("MVP 只支持单集群")
        if self.vendor_profiles is not None and len(self.vendor_profiles) != 1:
            raise ValidationError("MVP 只支持单 vendor profile")
        if self.model_configs is not None and len(self.model_configs) != 1:
            raise ValidationError("MVP 只支持单模型")


class ErrorType(StrEnum):
    """MVP 受控失败类型集合。"""

    BENCH_TIMEOUT = "BENCH_TIMEOUT"
    BENCH_COMMAND_FAILED = "BENCH_COMMAND_FAILED"
    RESULT_JSON_NOT_FOUND = "RESULT_JSON_NOT_FOUND"
    RESULT_PARSE_FAILED = "RESULT_PARSE_FAILED"
    TARGET_POD_PENDING = "TARGET_POD_PENDING"
    TARGET_POD_FAILED = "TARGET_POD_FAILED"
    TARGET_HEALTH_TIMEOUT = "TARGET_HEALTH_TIMEOUT"
    TARGET_SERVER_CRASHED = "TARGET_SERVER_CRASHED"
    TARGET_IMAGE_PULL_FAILED = "TARGET_IMAGE_PULL_FAILED"
    TARGET_POD_DELETE_TIMEOUT = "TARGET_POD_DELETE_TIMEOUT"
    TARGET_POD_FORCE_DELETED = "TARGET_POD_FORCE_DELETED"
    K8S_API_ERROR = "K8S_API_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


@dataclass(frozen=True)
class FailedCase:
    """写入 failed_cases.jsonl 的失败记录。

    字段集合来自 OpenSpec：必须能定位 run、case、target Pod、node、原始日志和时间范围。
    """

    run_id: str
    case_id: str
    serve_config: dict[str, Any]
    bench_config: dict[str, Any]
    attempt: int
    error_type: ErrorType | str
    error_message: str
    raw_log_path: str
    target_pod_name: str
    target_node_name: str
    start_time: str
    end_time: str

    def __post_init__(self) -> None:
        # failed case 是排障入口，核心定位字段不允许为空。
        for field_name in (
            "run_id",
            "case_id",
            "error_message",
            "raw_log_path",
            "target_pod_name",
            "target_node_name",
            "start_time",
            "end_time",
        ):
            _require_text(field_name, getattr(self, field_name))
        if self.attempt <= 0:
            raise ValidationError("attempt 必须大于 0")
        try:
            # 将字符串规范化成 ErrorType，避免 JSON 反序列化后失去枚举约束。
            error_type = ErrorType(self.error_type)
        except ValueError as exc:
            raise ValidationError("error_type 必须是 MVP 标准失败类型") from exc
        object.__setattr__(self, "error_type", error_type)
