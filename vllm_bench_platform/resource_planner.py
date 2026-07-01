from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
import time
from typing import Any, Callable
from urllib.parse import quote
from urllib.request import Request, urlopen

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


@dataclass(frozen=True)
class ModelResourceMetadata:
    attention_heads: int
    total_size_bytes: int
    source: str


FetchJson = Callable[[str, str | None], dict[str, Any]]
DEFAULT_FETCH_RETRIES = 3


def plan_model_resources(
    *,
    memory_per_gpu_gb: float,
    model_id: str | None = None,
    fallback_model_id: str | None = None,
    hf_endpoint: str = "https://huggingface.co",
    hf_token: str | None = None,
    fetch_json: FetchJson | None = None,
    overhead_factor: float = 1.8,
) -> ResourcePlan:
    if memory_per_gpu_gb <= 0:
        raise ResourcePlanningError("memory_per_gpu_gb must be greater than 0")
    if overhead_factor <= 0:
        raise ResourcePlanningError("overhead_factor must be greater than 0")
    metadata = load_model_resource_metadata(
        model_id=model_id,
        fallback_model_id=fallback_model_id,
        hf_endpoint=hf_endpoint,
        hf_token=hf_token,
        fetch_json=fetch_json,
    )

    model_weight_size_gb = metadata.total_size_bytes / 1e9
    estimated_vram_gb = model_weight_size_gb * overhead_factor
    gpu_count = math.ceil(estimated_vram_gb / memory_per_gpu_gb)
    if gpu_count > 1 and gpu_count % 2:
        gpu_count += 1

    tensor_parallel_size, pipeline_parallel_size = calculate_parallel_strategy(
        gpu_count=gpu_count,
        attention_heads=metadata.attention_heads,
    )
    return ResourcePlan(
        gpu_count=gpu_count,
        tensor_parallel_size=tensor_parallel_size,
        pipeline_parallel_size=pipeline_parallel_size,
        attention_heads=metadata.attention_heads,
        model_weight_size_gb=round(model_weight_size_gb, 6),
        estimated_vram_gb=round(estimated_vram_gb, 6),
    )


def load_model_resource_metadata(
    *,
    model_id: str | None = None,
    fallback_model_id: str | None = None,
    hf_endpoint: str = "https://huggingface.co",
    hf_token: str | None = None,
    fetch_json: FetchJson | None = None,
) -> ModelResourceMetadata:
    """从 Hugging Face 读取资源规划所需的模型元数据。"""
    errors: list[str] = []
    for candidate_model_id in _candidate_model_ids(model_id, fallback_model_id):
        try:
            return _load_hf_model_metadata(
                candidate_model_id,
                hf_endpoint=hf_endpoint,
                hf_token=hf_token,
                fetch_json=fetch_json or _fetch_json,
            )
        except ResourcePlanningError as exc:
            errors.append(str(exc))
    detail = "; ".join(errors) if errors else "MODEL_PATH or MODEL_NAME must be a Hugging Face repo id"
    raise ResourcePlanningError(f"failed to load model resource metadata: {detail}")


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


def _load_hf_model_metadata(
    model_id: str,
    *,
    hf_endpoint: str,
    hf_token: str | None,
    fetch_json: FetchJson,
) -> ModelResourceMetadata:
    quoted_id = quote(model_id.strip("/"), safe="/")
    endpoint = hf_endpoint.rstrip("/")
    config = fetch_json(f"{endpoint}/{quoted_id}/resolve/main/config.json", hf_token)
    heads = config.get("num_attention_heads") or config.get("n_head")
    if not isinstance(heads, int) or heads <= 0:
        raise ResourcePlanningError(f"{model_id} missing positive num_attention_heads in Hugging Face config")
    total_size = _load_hf_weight_size(model_id, quoted_id, endpoint, hf_token, fetch_json)
    return ModelResourceMetadata(
        attention_heads=heads,
        total_size_bytes=total_size,
        source=f"huggingface:{model_id}",
    )


def _load_hf_weight_size(
    model_id: str,
    quoted_id: str,
    endpoint: str,
    hf_token: str | None,
    fetch_json: FetchJson,
) -> int:
    info = fetch_json(f"{endpoint}/api/models/{quoted_id}?expand[]=siblings", hf_token)
    siblings = info.get("siblings", [])
    if not isinstance(siblings, list):
        raise ResourcePlanningError(f"{model_id} Hugging Face model info missing siblings")
    total = 0
    for item in siblings:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("rfilename", ""))
        if _is_weight_file(filename):
            size = item.get("size")
            if isinstance(size, int | float) and size > 0:
                total += int(size)
    if total > 0:
        return total

    for index_name in ("model.safetensors.index.json", "pytorch_model.bin.index.json"):
        try:
            index = fetch_json(f"{endpoint}/{quoted_id}/resolve/main/{index_name}", hf_token)
            return _total_size_from_index(index, f"{model_id}/{index_name}")
        except ResourcePlanningError:
            continue
    raise ResourcePlanningError(f"{model_id} Hugging Face model info missing weight file sizes")


def _total_size_from_index(data: dict[str, Any], label: str) -> int:
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        raise ResourcePlanningError(f"{label} missing metadata object")
    total_size = metadata.get("total_size")
    if not isinstance(total_size, int | float) or total_size <= 0:
        raise ResourcePlanningError(f"{label} missing positive metadata.total_size")
    return int(total_size)


def _is_weight_file(filename: str) -> bool:
    if filename.endswith(".safetensors") or filename.endswith(".bin"):
        return True
    return filename.endswith(".pt") or filename.endswith(".pth")


def _looks_like_hf_repo_id(value: str) -> bool:
    text = value.strip()
    if not text or "://" in text or text.startswith(("/", ".", "~")):
        return False
    return "\\" not in text and 1 <= text.count("/") <= 2


def _candidate_model_ids(*values: str | None) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and _looks_like_hf_repo_id(value) and value not in result:
            result.append(value)
    return result


def _fetch_json(url: str, token: str | None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    errors: list[str] = []
    for attempt in range(1, DEFAULT_FETCH_RETRIES + 1):
        try:
            with urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except Exception as exc:
            errors.append(f"attempt {attempt}: {exc}")
            if attempt < DEFAULT_FETCH_RETRIES:
                time.sleep(min(2 ** (attempt - 1), 4))
    else:
        raise ResourcePlanningError(f"failed to fetch {url} after {DEFAULT_FETCH_RETRIES} attempts: {'; '.join(errors)}")
    if not isinstance(data, dict):
        raise ResourcePlanningError(f"{url} must be a JSON object")
    return data


def _divisors(value: int) -> list[int]:
    return [candidate for candidate in range(1, value + 1) if value % candidate == 0]
