"""离线渲染 Kubernetes manifests。

render 路径和 submit 路径共用同一组 builder：先把 payload 解析成 RunConfig，再生成
Namespace、RBAC、PV、PVC、ConfigMap、Master Job。不同点是 render 只写 YAML 文件，
不会调用 kubectl，也不要求本地能访问测试线集群。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
from typing import Any

from vllm_bench_platform.backend.config_builder import build_config_map
from vllm_bench_platform.backend.job_builder import (
    MasterJobOptions,
    build_master_job,
    build_namespace,
    build_rbac_manifests,
    build_results_pv,
    build_results_pvc,
)
from vllm_bench_platform.backend.submit_job import SubmitJobRequest


DEFAULT_RENDER_ROOT = Path("manifests") / "generated"
_BARE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_PLAIN_STRING = re.compile(r"^[A-Za-z0-9_./:-][A-Za-z0-9_./:-]*$")
_NUMBER_LIKE = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
_YAML_RESERVED = {"null", "true", "false", "~", "yes", "no", "on", "off"}


@dataclass(frozen=True)
class RenderedManifestSet:
    """一次 render 的输出索引。"""

    run_id: str
    namespace: str
    output_dir: Path
    files: list[Path]


def build_ordered_manifests(
    payload: dict[str, Any],
    *,
    host_path: str,
    master_options: MasterJobOptions | None = None,
) -> list[tuple[str, list[dict[str, Any]]]]:
    """按远端 apply 顺序构造 manifest 文档组。"""
    request = SubmitJobRequest.from_payload(payload)
    run_config = request.run_config
    return [
        ("00-namespace.yaml", [build_namespace(run_config.namespace)]),
        ("01-rbac.yaml", build_rbac_manifests(run_config.namespace)),
        ("02-pv.yaml", [build_results_pv(run_config, host_path)]),
        ("03-pvc.yaml", [build_results_pvc(run_config)]),
        ("04-configmap.yaml", [build_config_map(run_config)]),
        ("05-master-job.yaml", [build_master_job(run_config, master_options)]),
    ]


def render_manifests(
    payload: dict[str, Any],
    *,
    host_path: str,
    output_dir: str | Path | None = None,
    master_options: MasterJobOptions | None = None,
) -> RenderedManifestSet:
    """把一次 run 的完整 Kubernetes 资源写入本地 YAML 目录。"""
    request = SubmitJobRequest.from_payload(payload)
    run_id = request.run_config.run_id
    namespace = request.run_config.namespace
    target_dir = Path(output_dir) if output_dir is not None else DEFAULT_RENDER_ROOT / run_id
    target_dir.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    for filename, documents in build_ordered_manifests(
        payload,
        host_path=host_path,
        master_options=master_options,
    ):
        path = target_dir / filename
        path.write_text(dump_yaml_documents(documents), encoding="utf-8")
        files.append(path)
    return RenderedManifestSet(run_id=run_id, namespace=namespace, output_dir=target_dir, files=files)


def dump_yaml_documents(documents: list[dict[str, Any]]) -> str:
    """将 manifest dict 序列化为 Kubernetes 可读 YAML。"""
    return "---\n" + "\n---\n".join(_dump_yaml_value(document, 0) for document in documents) + "\n"


def _dump_yaml_value(value: Any, indent: int) -> str:
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines: list[str] = []
        for key, item in value.items():
            prefix = " " * indent + _format_key(str(key)) + ":"
            if isinstance(item, dict | list) and item:
                lines.append(prefix)
                lines.append(_dump_yaml_value(item, indent + 2))
            else:
                lines.append(prefix + " " + _format_scalar(item))
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        lines = []
        for item in value:
            prefix = " " * indent + "-"
            if isinstance(item, dict | list) and item:
                lines.append(prefix)
                lines.append(_dump_yaml_value(item, indent + 2))
            else:
                lines.append(prefix + " " + _format_scalar(item))
        return "\n".join(lines)
    return " " * indent + _format_scalar(value)


def _format_key(value: str) -> str:
    if _BARE_KEY.match(value):
        return value
    return json.dumps(value, ensure_ascii=False)


def _format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, dict):
        return "{}" if not value else json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[]" if not value else json.dumps(value, ensure_ascii=False)
    text = str(value)
    lowered = text.lower()
    if (
        text
        and _PLAIN_STRING.match(text)
        and lowered not in _YAML_RESERVED
        and not _NUMBER_LIKE.match(text)
    ):
        return text
    return json.dumps(text, ensure_ascii=False)
