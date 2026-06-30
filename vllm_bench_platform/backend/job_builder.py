"""Master Job 和结果 PVC 的 Kubernetes manifest 构造器。

OpenSpec 已经明确：后端只创建 ConfigMap、PVC、Master Job；
target vLLM Pod 和 target Service 由 Master Pod 内的 master-controller 动态创建。
因此本模块只描述 Master Pod 这一层资源，不把 target 资源混进来。

维护约束：
- Master Pod 必须是双容器：master-controller 和 bench-runner。
- 两个容器共享同一个网络命名空间，因此 controller 可以访问 localhost:18080。
- bench-runner 不能通过 localhost 访问 target vLLM，因为 target 在另一个 Pod。
- target vLLM 访问入口必须由 master-controller 创建的 target Service 提供。
- Master Pod 的两个容器都不能申请国产卡 accelerator resource。
- 只有后续 target Pod builder 能写入 vendor_profile.resource_name/resource_count。
- /configs 挂载 ConfigMap，作为四个 JSON 配置文件的只读输入。
- /results 挂载 PVC，保存 summary、raw logs、server logs 和 best_config。
- /work 使用 emptyDir，只做容器间临时协作，不作为最终结果目录。
- PVC 默认 ReadWriteOnce，因为 MVP 只有同一个 Master Pod 内的两个容器写入。
- 多 Worker Pod 并发写结果时，必须通过新 OpenSpec change 重新评估存储设计。
- serviceAccountName 固定为 vllm-bench-master，和 RBAC manifest 保持一致。
- 镜像名当前是实现占位，后续发布流程可以通过配置或 Helm/Kustomize 覆盖。
- Job backoffLimit 为 0，是为了让 controller 自己记录失败，而不是让 Kubernetes 盲重试。
- 这里返回纯 dict，便于单元测试和 fake Kubernetes client 在无集群环境下验证。
- 不在此处创建 Namespace/RBAC，是因为它们属于 manifests 阶段的集群准备资源。
- 不在此处创建 target Service，是为了保持 backend 与 master-controller 的职责边界。
- 修改此模块时，优先检查 job-submission 和 bench-runner-agent 两组 OpenSpec 场景。
"""

from dataclasses import dataclass, field
from typing import Any

from vllm_bench_platform.schemas import RunConfig


@dataclass(frozen=True)
class MasterJobOptions:
    """Master Job 中由外部发布环境决定的运行参数。"""

    master_image: str = "vllm-bench-platform/master:latest"
    bench_runner_image: str = "vllm-bench-platform/bench-runner:latest"
    bench_command: str = "vllm bench serve"
    bench_timeout_seconds: int = 1800
    bench_num_prompts: int = 10
    bench_runner_health_timeout_seconds: int = 120
    bench_runner_request_timeout_seconds: int = 30
    master_memory_request: str = "256Mi"
    master_memory_limit: str = "512Mi"
    bench_runner_memory_request: str = "256Mi"
    bench_runner_memory_limit: str = "512Mi"
    pod_tolerations: list[dict[str, Any]] = field(default_factory=list)


def build_namespace(namespace: str) -> dict[str, Any]:
    """构造 smoke 提交时可重复 apply 的 Namespace。"""
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": namespace,
        },
    }


def build_results_pv(run_config: RunConfig, host_path: str) -> dict[str, Any]:
    """构造 hostPath PV。

    当前单节点 smoke 环境没有 StorageClass，因此用 hostPath 让 PVC 可以实际绑定。
    这只是最小验证路径，后续生产部署可以替换为集群默认存储类或对象存储。
    """
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolume",
        "metadata": {
            "name": f"vllm-bench-results-{run_config.run_id}",
        },
        "spec": {
            "capacity": {
                "storage": "20Gi",
            },
            "accessModes": ["ReadWriteOnce"],
            "persistentVolumeReclaimPolicy": "Retain",
            "storageClassName": "",
            "hostPath": {
                "path": host_path,
                "type": "DirectoryOrCreate",
            },
            "claimRef": {
                "namespace": run_config.namespace,
                "name": f"vllm-bench-results-{run_config.run_id}",
            },
        },
    }


def build_results_pvc(run_config: RunConfig) -> dict[str, Any]:
    """构造结果 PVC。

    MVP 中只有同一个 Master Pod 内的两个容器共享写入 `/results`，
    不存在多个 Pod 并发写同一个卷，所以默认 ReadWriteOnce。
    后续如果引入多个 Worker Pod，再通过新的 OpenSpec change 改成 RWX 或对象存储。
    """
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": f"vllm-bench-results-{run_config.run_id}",
            "namespace": run_config.namespace,
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": "",
            "volumeName": f"vllm-bench-results-{run_config.run_id}",
            "resources": {
                "requests": {
                    "storage": "20Gi",
                },
            },
        },
    }


def build_rbac_manifests(namespace: str) -> list[dict[str, Any]]:
    """构造 Master Pod 操作 target 生命周期所需的最小 RBAC。"""
    service_account = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": "vllm-bench-master",
            "namespace": namespace,
        },
    }
    role = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {
            "name": "vllm-bench-master",
            "namespace": namespace,
        },
        "rules": [
            {
                "apiGroups": [""],
                "resources": ["pods"],
                "verbs": ["get", "list", "watch", "create", "delete", "patch"],
            },
            {
                "apiGroups": [""],
                "resources": ["pods/log"],
                "verbs": ["get", "list"],
            },
            {
                "apiGroups": [""],
                "resources": ["services"],
                "verbs": ["get", "list", "watch", "create", "delete", "patch"],
            },
            {
                "apiGroups": [""],
                "resources": ["events"],
                "verbs": ["get", "list", "watch"],
            },
            {
                "apiGroups": [""],
                "resources": ["configmaps"],
                "verbs": ["get", "list"],
            },
            {
                "apiGroups": ["batch"],
                "resources": ["jobs"],
                "verbs": ["get", "list", "watch"],
            },
        ],
    }
    role_binding = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {
            "name": "vllm-bench-master",
            "namespace": namespace,
        },
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": "vllm-bench-master",
                "namespace": namespace,
            }
        ],
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "Role",
            "name": "vllm-bench-master",
        },
    }
    return [service_account, role, role_binding]


def build_master_job(
    run_config: RunConfig,
    options: MasterJobOptions | None = None,
) -> dict[str, Any]:
    """构造双容器 Master Job。

    Master Pod 是一次压测 run 的执行单元，包含：
    - master-controller：控制 target vLLM Pod/Service 生命周期。
    - bench-runner：在 localhost:18080 暴露轻量 vllm-bench agent。

    这两个容器都不能申请国产卡资源；只有 target vLLM Pod 可以申请 accelerator。
    """
    options = options or MasterJobOptions()
    config_map_name = f"vllm-bench-config-{run_config.run_id}"
    pvc_name = f"vllm-bench-results-{run_config.run_id}"
    # 三个挂载点分别对应配置输入、结果输出和临时协作目录。
    # /work 使用 emptyDir，避免临时文件污染最终结果目录。
    volumes = [
        {
            "name": "configs",
            "configMap": {
                "name": config_map_name,
            },
        },
        {
            "name": "results",
            "persistentVolumeClaim": {
                "claimName": pvc_name,
            },
        },
        {
            "name": "work",
            "emptyDir": {},
        },
    ]
    volume_mounts = [
        {"name": "configs", "mountPath": "/configs"},
        {"name": "results", "mountPath": "/results"},
        {"name": "work", "mountPath": "/work"},
    ]
    # 镜像名先使用占位默认值，真正镜像仓库会在后续部署/CI change 中确认。
    # 当前 builder 的重点是锁定 Pod 结构、挂载和资源申请规则。
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": f"vllm-bench-master-{run_config.run_id}",
            "namespace": run_config.namespace,
        },
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {
                        "app": "vllm-bench-master",
                        "run_id": run_config.run_id,
                    },
                },
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": "vllm-bench-master",
                    "volumes": volumes,
                    "tolerations": [dict(item) for item in options.pod_tolerations],
                    "containers": [
                        {
                            "name": "master-controller",
                            "image": options.master_image,
                            "command": [
                                "python3",
                                "-m",
                                "vllm_bench_platform.master.master",
                            ],
                            "env": [
                                {"name": "RUN_ID", "value": run_config.run_id},
                                {"name": "NAMESPACE", "value": run_config.namespace},
                                {
                                    "name": "BENCH_RUNNER_HEALTH_TIMEOUT_SECONDS",
                                    "value": str(options.bench_runner_health_timeout_seconds),
                                },
                                {
                                    "name": "BENCH_RUNNER_REQUEST_TIMEOUT_SECONDS",
                                    "value": str(options.bench_runner_request_timeout_seconds),
                                },
                            ],
                            "volumeMounts": volume_mounts,
                            # 控制容器只需要 CPU/内存，不允许出现 vendor.com/xpu 等 accelerator。
                            "resources": {
                                "requests": {
                                    "cpu": "100m",
                                    "memory": options.master_memory_request,
                                },
                                "limits": {
                                    "memory": options.master_memory_limit,
                                },
                            },
                        },
                        {
                            "name": "bench-runner",
                            "image": options.bench_runner_image,
                            "command": [
                                "python3",
                                "-m",
                                "vllm_bench_platform.bench_runner.bench_agent",
                            ],
                            "env": [
                                {"name": "BENCH_COMMAND", "value": options.bench_command},
                                {"name": "BENCH_TIMEOUT_SECONDS", "value": str(options.bench_timeout_seconds)},
                                {"name": "BENCH_NUM_PROMPTS", "value": str(options.bench_num_prompts)},
                            ],
                            # 18080 只在 Pod 网络命名空间内给 master-controller 调用，
                            # 不需要额外创建 Service 暴露 bench-runner。
                            "ports": [
                                {
                                    "containerPort": 18080,
                                    "name": "http",
                                }
                            ],
                            "volumeMounts": volume_mounts,
                            # bench-runner 是压测客户端，不加载模型权重，也不申请国产卡资源。
                            "resources": {
                                "requests": {
                                    "cpu": "100m",
                                    "memory": options.bench_runner_memory_request,
                                },
                                "limits": {
                                    "memory": options.bench_runner_memory_limit,
                                },
                            },
                        },
                    ],
                },
            },
        },
    }
