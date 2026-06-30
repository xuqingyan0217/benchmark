"""后端 submit-job 编排入口。

这个模块位于后端边界：它接收用户提交的 payload，先转换成共享 RunConfig，
再通过 builder 生成 Kubernetes 资源，最后调用注入的 Kubernetes client。
非法输入必须在任何资源创建前失败，这是 OpenSpec 对任务提交的关键约束。

维护约束：
- submit_run 是后端提交路径的最小闭环，后续 API 层可以直接调用它。
- payload 进入后必须先解析成 RunConfig，不能边解析边创建 Kubernetes 资源。
- 如果 `_benchmark_name` 缺失，必须在 create_config_map/create_pvc/create_job 前失败。
- ConfigMap、PVC、Master Job 创建顺序不能随意调整，Job 最后创建才能看到依赖资源。
- KubernetesSubmitClient 是协议而不是具体实现，方便 fake client 做无集群测试。
- SubmitJobResponse 只返回资源身份，不承诺运行已经完成。
- 后续任务状态查询应读取 Job/PVC 状态，不应该让 submit_run 阻塞等待压测完成。
- submit_run 不执行 bench，也不直接创建 target Pod；这些都属于 Master Job 内部职责。
- 请求 payload 里的 serve_hparams 和 bench_hparams 保留原始 flag，是为了支持 vLLM 扩展参数。
- 本模块不做用户权限、认证和租户隔离；这些属于后续非 MVP 平台能力。
- 本模块不绑定 FastAPI，是为了先稳定业务契约，再决定 HTTP 框架细节。
- 如果后续把 payload schema 搬到 Web 框架模型中，也必须保留当前资源创建前校验语义。
- 这里的注释强调失败前不落资源，因为半创建资源是 Kubernetes 平台最常见的清理负担。
"""

from dataclasses import dataclass
from typing import Any, Protocol

from vllm_bench_platform.backend.config_builder import build_config_map
from vllm_bench_platform.backend.job_builder import (
    MasterJobOptions,
    build_master_job,
    build_results_pvc,
)
from vllm_bench_platform.schemas import (
    BenchConfig,
    ModelConfig,
    RunConfig,
    ServeConfig,
    VendorProfile,
)


class KubernetesSubmitClient(Protocol):
    """后端提交阶段需要的最小 Kubernetes client 协议。

    使用 Protocol 是为了让生产 client 和测试 fake client 共享同一接口；
    当前 MVP 不直接绑定官方 Kubernetes client，避免早期测试依赖真实集群。
    """

    def create_config_map(self, manifest: dict[str, Any]) -> None: ...

    def create_pvc(self, manifest: dict[str, Any]) -> None: ...

    def create_job(self, manifest: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class SubmitJobRequest:
    """用户提交请求的规范化形态。"""

    run_config: RunConfig

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SubmitJobRequest":
        """把 API payload 转成强约束 RunConfig。

        serve_hparams / bench_hparams 中除 `_benchmark_name` 以外的字段都保留为
        CLI 参数 map，避免 builder 层丢失用户提交的 vLLM/vllm-bench 参数。
        """
        serve_configs = [
            _matrix_config_from_payload(item, ServeConfig)
            for item in payload.get("serve_hparams", [])
        ]
        bench_configs = [
            _matrix_config_from_payload(item, BenchConfig)
            for item in payload.get("bench_hparams", [])
        ]
        vendor_profile = VendorProfile(**payload["vendor_profile"])
        model_config = ModelConfig(**payload["model_config"])
        run_config = RunConfig(
            run_id=payload["run_id"],
            namespace=payload["namespace"],
            clusters=payload.get("clusters", ["default"]),
            serve_configs=serve_configs,
            bench_configs=bench_configs,
            vendor_profile=vendor_profile,
            model_config=model_config,
        )
        return cls(run_config=run_config)


@dataclass(frozen=True)
class SubmitJobResponse:
    """submit 成功后返回给调用方的资源身份。"""

    run_id: str
    namespace: str
    config_map_name: str
    pvc_name: str
    master_job_name: str


def submit_run(
    payload: dict[str, Any],
    kubernetes_client: KubernetesSubmitClient,
    master_options: MasterJobOptions | None = None,
) -> SubmitJobResponse:
    """提交一次 MVP run。

    函数顺序刻意保持为：先完整解析和校验 payload，再创建任何 Kubernetes 资源。
    这样缺失 `_benchmark_name` 或超出 MVP fan-out 时，不会留下半创建的 ConfigMap/PVC/Job。
    """
    request = SubmitJobRequest.from_payload(payload)
    run_id = request.run_config.run_id
    namespace = request.run_config.namespace

    config_map = build_config_map(request.run_config)
    pvc = build_results_pvc(request.run_config)
    job = build_master_job(request.run_config, master_options)

    # 创建顺序与 OpenSpec 保持一致：配置先存在，再准备结果卷，最后启动 Master Job。
    kubernetes_client.create_config_map(config_map)
    kubernetes_client.create_pvc(pvc)
    kubernetes_client.create_job(job)

    return SubmitJobResponse(
        run_id=run_id,
        namespace=namespace,
        config_map_name=config_map["metadata"]["name"],
        pvc_name=pvc["metadata"]["name"],
        master_job_name=job["metadata"]["name"],
    )


def _matrix_config_from_payload(
    payload: dict[str, Any],
    config_type: type[ServeConfig] | type[BenchConfig],
) -> ServeConfig | BenchConfig:
    """从矩阵项中拆出 `_benchmark_name` 和原始参数。"""
    payload = dict(payload)
    benchmark_name = payload.pop("_benchmark_name", "")
    return config_type(_benchmark_name=benchmark_name, params=payload)
