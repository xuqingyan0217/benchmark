"""master-controller 的 kubectl client。

维护约束：
- 这个 client 只服务 Master Pod 内的 target 生命周期，不负责后端 submit 阶段资源。
- 使用 kubectl 是当前最小闭环选择，方便在已有 kubeconfig/in-cluster 环境中快速验证。
- 所有方法都可被 fake client 替换，集成测试不需要真实 Kubernetes。
- `create_pod` 和 `create_service` 使用 apply，便于重复调试同一个 run_id 时覆盖资源形状。
- Pod ready 等待依赖 Kubernetes Ready condition；target HTTP health 另由 `wait_http_ready` 检查。
- events/logs 抓取失败不应阻断清理，因此相关方法返回错误文本。
- delete 使用 `--ignore-not-found`，清理路径可以安全重复调用。
- Pod 删除超时后尝试 force delete，以降低国产卡资源长时间占用风险。
- force delete 失败返回 False，controller 后续可据此记录 `TARGET_POD_FORCE_DELETED` 或 timeout。
- 这里不封装 Namespace/RBAC/PVC，它们属于 backend submit 和 manifests 阶段。
- 如果迁移官方 client，应保留这些方法名，减少 master.py 的改动面。
"""

from __future__ import annotations

import json
import http.client
import subprocess
import time
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import urlopen

from vllm_bench_platform.backend.kubectl_client import KubectlRunner, run_kubectl


FATAL_CONTAINER_REASONS = {
    "CrashLoopBackOff",
    "ErrImagePull",
    "ImagePullBackOff",
    "InvalidImageName",
    "CreateContainerConfigError",
    "CreateContainerError",
    "RunContainerError",
    "OOMKilled",
    "Error",
}


class KubectlMasterClient:
    """封装 target Pod/Service 生命周期需要的 kubectl 操作。"""

    def __init__(self, runner: KubectlRunner = run_kubectl):
        self._runner = runner

    def create_pod(self, manifest: dict[str, Any]) -> None:
        self._apply(manifest)

    def create_service(self, manifest: dict[str, Any]) -> None:
        self._apply(manifest)

    def wait_pod_ready(self, name: str, namespace: str, timeout_seconds: int = 600) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                pod = self._get_pod(name, namespace)
            except Exception:
                time.sleep(2)
                continue
            phase = pod.get("status", {}).get("phase", "")
            if phase == "Failed":
                return False
            if _has_fatal_container_state(pod):
                return False
            for condition in pod.get("status", {}).get("conditions", []):
                if condition.get("type") == "Ready" and condition.get("status") == "True":
                    return True
            time.sleep(2)
        return False

    def wait_http_ready(self, url: str, timeout_seconds: int = 600) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                with urlopen(url, timeout=5) as response:
                    if 200 <= response.status < 500:
                        return True
            except (URLError, TimeoutError, http.client.RemoteDisconnected):
                time.sleep(2)
        return False

    def wait_target_http_ready(self, url: str, pod_name: str, namespace: str, timeout_seconds: int = 600) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                pod = self._get_pod(pod_name, namespace)
                phase = pod.get("status", {}).get("phase", "")
                if phase == "Failed" or _has_fatal_container_state(pod):
                    return False
            except Exception:
                pass
            try:
                with urlopen(url, timeout=5) as response:
                    if 200 <= response.status < 500:
                        return True
            except (URLError, TimeoutError, http.client.RemoteDisconnected):
                time.sleep(2)
        return False

    def pod_node_name(self, name: str, namespace: str) -> str:
        pod = self._get_pod(name, namespace)
        return pod.get("spec", {}).get("nodeName", "")

    def pod_phase(self, name: str, namespace: str) -> str:
        pod = self._get_pod(name, namespace)
        return pod.get("status", {}).get("phase", "")

    def pod_failure_reason(self, name: str, namespace: str) -> str:
        pod = self._get_pod(name, namespace)
        return _pod_failure_reason(pod)

    def get_pod_logs(self, name: str, namespace: str) -> str:
        try:
            return self._runner(["kubectl", "logs", name, "-n", namespace], None, None)
        except Exception as exc:
            return str(exc)

    def get_pod_events(self, name: str, namespace: str) -> str:
        try:
            return self._runner(
                [
                    "kubectl",
                    "get",
                    "events",
                    "-n",
                    namespace,
                    "--field-selector",
                    f"involvedObject.name={name}",
                    "-o",
                    "json",
                ],
                None,
                None,
            )
        except Exception as exc:
            return str(exc)

    def delete_service(self, name: str, namespace: str) -> None:
        self._delete("service", name, namespace)

    def delete_pod(self, name: str, namespace: str) -> None:
        self._delete("pod", name, namespace)

    def wait_pod_deleted(self, name: str, namespace: str, timeout_seconds: int = 120) -> bool:
        try:
            self._runner(
                ["kubectl", "wait", "--for=delete", f"pod/{name}", "-n", namespace, f"--timeout={timeout_seconds}s"],
                None,
                timeout_seconds + 10,
            )
            return True
        except Exception:
            try:
                self._runner(["kubectl", "delete", "pod", name, "-n", namespace, "--force", "--grace-period=0"], None, None)
            except Exception:
                return False
            return True

    def _apply(self, manifest: dict[str, Any]) -> None:
        self._runner(["kubectl", "apply", "-f", "-"], json.dumps(manifest, ensure_ascii=False), None)

    def _get_pod(self, name: str, namespace: str) -> dict[str, Any]:
        output = self._runner(["kubectl", "get", "pod", name, "-n", namespace, "-o", "json"], None, None)
        return json.loads(output)

    def _delete(self, kind: str, name: str, namespace: str) -> None:
        try:
            self._runner(["kubectl", "delete", kind, name, "-n", namespace, "--ignore-not-found=true"], None, None)
        except subprocess.SubprocessError:
            raise


def _has_fatal_container_state(pod: dict[str, Any]) -> bool:
    return bool(_pod_failure_reason(pod))


def _pod_failure_reason(pod: dict[str, Any]) -> str:
    status = pod.get("status", {})
    container_statuses = []
    container_statuses.extend(status.get("containerStatuses", []) or [])
    container_statuses.extend(status.get("initContainerStatuses", []) or [])
    for container in container_statuses:
        state = container.get("state", {})
        waiting_reason = state.get("waiting", {}).get("reason")
        terminated_reason = state.get("terminated", {}).get("reason")
        last_terminated_reason = container.get("lastState", {}).get("terminated", {}).get("reason")
        if waiting_reason in FATAL_CONTAINER_REASONS:
            return str(waiting_reason)
        if terminated_reason in FATAL_CONTAINER_REASONS:
            return str(terminated_reason)
        if last_terminated_reason in FATAL_CONTAINER_REASONS:
            return str(last_terminated_reason)
    return ""
