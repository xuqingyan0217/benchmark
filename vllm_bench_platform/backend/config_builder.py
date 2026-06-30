"""ConfigMap manifest 构造器。

本模块只负责把已经通过共享 schema 校验的 run 配置序列化进
Kubernetes ConfigMap。这里不做业务校验，是为了让职责边界清楚：
校验属于 schema / submit 层，Kubernetes 资源形状属于 builder 层。

四个 key 名称必须和 OpenSpec 中的配置文件名完全一致，因为 Master Pod
后续会把 ConfigMap 挂载到 `/configs`，master-controller 依赖这些文件名读取矩阵。

维护约束：
- serve_hparams.json 保存服务端参数，变化时会导致 target Pod 重建。
- bench_hparams.json 保存请求参数，变化时不应该导致 target Pod 重建。
- vendor_profile.json 保存国产卡厂商差异，后续 target Pod builder 会读取它。
- model_config.json 保存模型加载信息，后续 vLLM server 启动命令会读取它。
- ConfigMap data value 必须是字符串，因此这里统一 JSON 序列化。
- sort_keys=True 让输出稳定，方便测试和排查 manifest diff。
- ensure_ascii=False 保留中文，方便用户配置中出现中文注释或名称时排障。
- 本模块不负责压缩配置，也不负责引用外部对象存储；MVP 以 ConfigMap 为契约。
- 如果配置体积超过 Kubernetes ConfigMap 限制，需要通过新的 OpenSpec change 调整方案。
- 这里不生成 volumeMount，volumeMount 属于 Master Job builder 的职责。
"""

from dataclasses import asdict
import json
from typing import Any

from vllm_bench_platform.schemas import RunConfig


def build_config_map(run_config: RunConfig) -> dict[str, Any]:
    """构造保存四个 MVP 配置文件的 ConfigMap。

    这里使用 JSON 字符串而不是 Python dict，是为了贴近 Kubernetes ConfigMap
    的实际 data 字段语义：每个 key 都是一个挂载后的文件，每个 value 都是文件内容。
    `ensure_ascii=False` 保留中文，方便后续排查用户提交的中文描述或错误信息。
    """
    # serve_hparams.json 和 bench_hparams.json 需要保留 CLI flag 原始 key，
    # 因为它们后续会被拼接成 vLLM serve / vllm-bench 命令行参数。
    data = {
        "serve_hparams.json": json.dumps(
            [item.as_cli_args() for item in run_config.serve_configs],
            ensure_ascii=False,
            sort_keys=True,
        ),
        "bench_hparams.json": json.dumps(
            [item.as_cli_args() for item in run_config.bench_configs],
            ensure_ascii=False,
            sort_keys=True,
        ),
        "vendor_profile.json": json.dumps(
            asdict(run_config.vendor_profile),
            ensure_ascii=False,
            sort_keys=True,
        ),
        "model_config.json": json.dumps(
            asdict(run_config.model_config),
            ensure_ascii=False,
            sort_keys=True,
        ),
    }
    # manifest 返回纯 dict，暂不绑定 Kubernetes Python client 类型；
    # 这样单元测试和后续 fake client 都可以在无集群环境下验证资源形状。
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": f"vllm-bench-config-{run_config.run_id}",
            "namespace": run_config.namespace,
        },
        "data": data,
    }
