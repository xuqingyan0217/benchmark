"""基于 kubectl 的最小 Kubernetes 提交客户端。

当前环境已经有可用 kubectl，而项目尚未引入 Kubernetes Python client；这里用
`kubectl apply -f -` 保持 smoke 闭环可运行，同时让测试可以注入 fake runner。

维护约束：
- 这个 client 是最小闭环工具，不是长期的 Kubernetes SDK 抽象终点。
- 使用 stdin apply 是为了避免在仓库或容器内留下临时 manifest 文件。
- manifest 以 JSON 写入 stdin，kubectl 同样接受 JSON/YAML，这让实现只依赖标准库。
- runner 可注入，单元测试不需要真实集群，也不会误创建资源。
- `create_config_map`、`create_pvc`、`create_job` 保持 submit_job 的协议名称，避免把
  kubectl 细节泄漏到 submit 编排层。
- Namespace、RBAC、PV 这类前置资源通过 `apply_manifest` 显式调用，不混进
  `submit_run` 的 ConfigMap/PVC/Job 最小契约。
- 失败时抛出 stderr，调用方应把错误返回给运维人员，不要静默吞掉。
- 这里不实现 delete，清理由 master-controller 的 client 或人工 smoke 命令处理。
- 不默认追加 namespace 参数，因为 manifest 自身已经携带 namespace，Namespace/PV 又是
  集群级资源。
- 后续迁移官方 Kubernetes client 时，应保留同样的可注入测试边界。
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Callable


KubectlRunner = Callable[[list[str], str | None, int | None], str]


def run_kubectl(args: list[str], input_text: str | None = None, timeout: int | None = None) -> str:
    """执行 kubectl 并返回 stdout，失败时保留 stderr 供调用方定位。"""
    completed = subprocess.run(
        args,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout


class KubectlSubmitClient:
    """实现 submit 阶段需要的资源创建协议。"""

    def __init__(self, runner: KubectlRunner = run_kubectl):
        self._runner = runner

    def apply_manifest(self, manifest: dict[str, Any]) -> None:
        """通过 stdin apply 单个 manifest，避免创建临时 YAML 文件。"""
        self._runner(
            ["kubectl", "apply", "-f", "-"],
            json.dumps(manifest, ensure_ascii=False),
            None,
        )

    def create_config_map(self, manifest: dict[str, Any]) -> None:
        self.apply_manifest(manifest)

    def create_pvc(self, manifest: dict[str, Any]) -> None:
        self.apply_manifest(manifest)

    def create_job(self, manifest: dict[str, Any]) -> None:
        self.apply_manifest(manifest)
