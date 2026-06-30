from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
from typing import Any

from vllm_bench_platform.schemas import RunConfig


class ResourcePlanningError(ValueError):
    pass


@dataclass(frozen=True)
class ResourcePlan:
    gpu_count: int
    tensor_parallel_size: int
    pipeline_parallel_size: int
    attention_heads: int
    model_weight_size_gb: float
    estimated_vram_gb: float


def plan_model_resources(
    model_metadata_path: str | Path,
    *,
    memory_per_gpu_gb: float,
    overhead_factor: float = 1.8,
) -> ResourcePlan:
    root = Path(model_metadata_path)
    total_size_bytes = _load_total_weight_size(root / "model.safetensors.index.json")
    attention_heads = _load_attention_heads(root / "config.json")
    if memory_per_gpu_gb <= 0:
        raise ResourcePlanningError("memory_per_gpu_gb must be greater than 0")
    if overhead_factor <= 0:
        raise ResourcePlanningError("overhead_factor must be greater than 0")

    model_weight_size_gb = total_size_bytes / 1e9
    estimated_vram_gb = model_weight_size_gb * overhead_factor
    gpu_count = math.ceil(estimated_vram_gb / memory_per_gpu_gb)
    if gpu_count > 1 and gpu_count % 2:
        gpu_count += 1

    tensor_parallel_size, pipeline_parallel_size = calculate_parallel_strategy(
        gpu_count=gpu_count,
        attention_heads=attention_heads,
    )
    return ResourcePlan(
        gpu_count=gpu_count,
        tensor_parallel_size=tensor_parallel_size,
        pipeline_parallel_size=pipeline_parallel_size,
        attention_heads=attention_heads,
        model_weight_size_gb=round(model_weight_size_gb, 6),
        estimated_vram_gb=round(estimated_vram_gb, 6),
    )


def calculate_parallel_strategy(*, gpu_count: int, attention_heads: int) -> tuple[int, int]:
    if gpu_count <= 0:
        raise ResourcePlanningError("gpu_count must be greater than 0")
    if attention_heads <= 0:
        raise ResourcePlanningError("num_attention_heads must be greater than 0")

    for candidate in sorted(_divisors(gpu_count), reverse=True):
        if attention_heads % candidate == 0:
            return candidate, gpu_count // candidate
    raise ResourcePlanningError("no valid tensor parallel size divides num_attention_heads")


def apply_resource_plan(run_config: RunConfig, plan: ResourcePlan) -> RunConfig:
    vendor_profile = replace(
        run_config.vendor_profile,
        resource_count=plan.gpu_count,
        tensor_parallel_size=plan.tensor_parallel_size,
        pipeline_parallel_size=plan.pipeline_parallel_size,
    )
    return replace(run_config, vendor_profile=vendor_profile)


def _load_total_weight_size(index_path: Path) -> int:
    data = _load_json_object(index_path)
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        raise ResourcePlanningError(f"{index_path} missing metadata object")
    total_size = metadata.get("total_size")
    if not isinstance(total_size, int | float) or total_size <= 0:
        raise ResourcePlanningError(f"{index_path} missing positive metadata.total_size")
    return int(total_size)


def _load_attention_heads(config_path: Path) -> int:
    data = _load_json_object(config_path)
    heads = data.get("num_attention_heads")
    if not isinstance(heads, int) or heads <= 0:
        raise ResourcePlanningError(f"{config_path} missing positive num_attention_heads")
    return heads


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ResourcePlanningError(f"{path} does not exist")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ResourcePlanningError(f"{path} must be a JSON object")
    return data


def _divisors(value: int) -> list[int]:
    return [candidate for candidate in range(1, value + 1) if value % candidate == 0]
