"""Master Pod 挂载配置加载器。

维护约束：
- loader 是 master-controller 的第一步，必须在创建任何 target 资源前完成。
- 四个文件名固定来自 ConfigMap builder，改名会破坏 `/configs` 挂载契约。
- serve/bench 矩阵项仍复用共享 schema 校验，保证 `_benchmark_name` 缺失时提前失败。
- bench 参数在这里也做 reference 拼写兼容，因为 ConfigMap 可能来自示例文件而不是 CLI。
- vendor_profile 和 model_config 保持单对象，不支持 MVP 外多 vendor/多模型 fan-out。
- loader 不读取 Kubernetes API，只消费本地挂载文件，便于在无集群测试中复用。
- run_id 和 namespace 来自 Master Job 环境变量，而不是配置文件，避免用户配置伪造资源身份。
- 这里不展开笛卡尔积，只返回 RunConfig；执行循环负责顺序和失败策略。
- 如果后续配置变成数据库或对象存储，仍应保留这个文件契约作为 ConfigMap 模式。
"""

from __future__ import annotations

import json
from pathlib import Path

from vllm_bench_platform.backend.runtime_config import normalize_bench_params
from vllm_bench_platform.schemas import BenchConfig, ModelConfig, RunConfig, ServeConfig, VendorProfile


def load_run_config_from_dir(config_dir: str | Path, *, run_id: str, namespace: str) -> RunConfig:
    """从 `/configs` 目录读取四个 MVP 配置文件并恢复 RunConfig。"""
    root = Path(config_dir)
    serve_configs = [
        ServeConfig(_benchmark_name=item.pop("_benchmark_name", ""), params=item)
        for item in _load_list(root / "serve_hparams.json")
    ]
    bench_configs = [
        BenchConfig(_benchmark_name=item.pop("_benchmark_name", ""), params=normalize_bench_params(item))
        for item in _load_list(root / "bench_hparams.json")
    ]
    vendor_profile = VendorProfile(**_load_object(root / "vendor_profile.json"))
    model_config = ModelConfig(**_load_object(root / "model_config.json"))
    return RunConfig(
        run_id=run_id,
        namespace=namespace,
        serve_configs=serve_configs,
        bench_configs=bench_configs,
        vendor_profile=vendor_profile,
        model_config=model_config,
    )


def _load_list(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path.name} 必须是 JSON array")
    return [dict(item) for item in data]


def _load_object(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} 必须是 JSON object")
    return dict(data)
