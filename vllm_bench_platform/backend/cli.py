"""后端最小命令行入口。

CLI 让开发者在没有 HTTP 服务的情况下提交 smoke run、查询状态和查看结果；它是
OpenSpec 中后端 API 的本地可运行替身，不承担 master-controller 的执行逻辑。

维护约束：
- `submit` 会创建 Namespace、hostPath PV、RBAC、ConfigMap、PVC、Master Job，足以让
  smoke run 在当前单节点集群启动。
- CLI 不执行 bench，也不等待 Job 完成；等待和日志查看交给 kubectl。
- `status`、`results`、`failed-cases` 都是只读命令，不应修改集群或结果目录。
- `--env` 是本地 smoke 的主后端参数模拟源，真实 API 接入后不应要求用户手工编辑它。
- `--serve-configs` 和 `--bench-configs` 可以指向完整 reference 或 smoke 子集。
- run_id 可指定，方便重复定位 Job 和结果目录；不指定则由 runtime_config 生成。
- submit 前先构造 `SubmitJobRequest`，保证非法 payload 在任何资源 apply 前失败。
- 前置资源 apply 使用幂等语义，便于重复 smoke；Master Job 名称仍要求 run_id 唯一。
- 这里故意不引入 FastAPI/uvicorn，避免最小闭环被 Web 框架依赖阻塞。
- 后续 HTTP API 可以薄封装这些函数，而不是复制 submit/query 逻辑。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vllm_bench_platform.backend.job_builder import (
    MasterJobOptions,
    build_namespace,
    build_rbac_manifests,
    build_results_pv,
)
from vllm_bench_platform.backend.kubectl_client import KubectlSubmitClient
from vllm_bench_platform.backend.manifest_renderer import render_manifests
from vllm_bench_platform.backend.persist_paths import results_query_root, run_host_path
from vllm_bench_platform.backend.query import get_run_status, list_result_files, read_failed_cases
from vllm_bench_platform.backend.runtime_config import build_payload_from_files, load_env_config
from vllm_bench_platform.backend.submit_job import SubmitJobRequest, submit_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vllm-bench-backend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("--env", required=True)
    submit_parser.add_argument("--serve-configs", required=True)
    submit_parser.add_argument("--bench-configs", required=True)
    submit_parser.add_argument("--run-id")

    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--env", required=True)
    render_parser.add_argument("--serve-configs", required=True)
    render_parser.add_argument("--bench-configs", required=True)
    render_parser.add_argument("--run-id")
    render_parser.add_argument("--output-dir")

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--env", required=True)
    status_parser.add_argument("--run-id", required=True)

    results_parser = subparsers.add_parser("results")
    results_parser.add_argument("--env", required=True)
    results_parser.add_argument("--run-id", required=True)

    failed_parser = subparsers.add_parser("failed-cases")
    failed_parser.add_argument("--env", required=True)
    failed_parser.add_argument("--run-id", required=True)

    args = parser.parse_args(argv)
    env = load_env_config(args.env)

    if args.command in {"submit", "render"}:
        payload = build_payload_from_files(
            env,
            args.serve_configs,
            args.bench_configs,
            run_id=args.run_id,
        )

    if args.command == "render":
        host_path = run_host_path(env.persist_root, payload["namespace"], payload["run_id"])
        rendered = render_manifests(
            payload,
            host_path=host_path,
            output_dir=args.output_dir,
            master_options=_master_options_from_env(env),
        )
        print(
            json.dumps(
                {
                    "run_id": rendered.run_id,
                    "namespace": rendered.namespace,
                    "output_dir": str(rendered.output_dir),
                    "files": [str(path) for path in rendered.files],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    if args.command == "submit":
        request = SubmitJobRequest.from_payload(payload)
        host_path = run_host_path(env.persist_root, request.run_config.namespace, request.run_config.run_id)
        client = KubectlSubmitClient()
        client.apply_manifest(build_namespace(request.run_config.namespace))
        client.apply_manifest(build_results_pv(request.run_config, host_path))
        for manifest in build_rbac_manifests(request.run_config.namespace):
            client.apply_manifest(manifest)
        response = submit_run(
            payload,
            client,
            master_options=_master_options_from_env(env),
        )
        print(json.dumps(response.__dict__, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "status":
        print(json.dumps(get_run_status(args.run_id, env.namespace, results_query_root(env.persist_root, env.namespace)), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "results":
        print(json.dumps(list_result_files(args.run_id, results_query_root(env.persist_root, env.namespace)), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "failed-cases":
        print(json.dumps(read_failed_cases(args.run_id, results_query_root(env.persist_root, env.namespace)), ensure_ascii=False, sort_keys=True))
        return 0
    return 1


def _master_options_from_env(env) -> MasterJobOptions:
    return MasterJobOptions(
        master_image=env.master_image,
        bench_binary=env.bench_binary,
        bench_timeout_seconds=env.bench_timeout_seconds,
        bench_num_prompts=env.bench_num_prompts,
        master_memory_request=env.master_memory_request,
        master_memory_limit=env.master_memory_limit,
        pod_tolerations=env.pod_tolerations,
        model_metadata_host_path=env.model_metadata_host_path,
        target_gpu_memory_gb=env.target_gpu_memory_gb,
    )


if __name__ == "__main__":
    raise SystemExit(main())
