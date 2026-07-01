import csv
import json
from pathlib import Path
import tempfile
import unittest


class MasterComponentsTest(unittest.TestCase):
    def test_matrix_loader_reads_config_files_and_normalizes_bench_params(self):
        from vllm_bench_platform.master.matrix_loader import load_run_config_from_dir

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "serve_hparams.json").write_text(
                json.dumps([{"_benchmark_name": "s1", "--max-num-seqs": 8}]),
                encoding="utf-8",
            )
            (config_dir / "bench_hparams.json").write_text(
                json.dumps([{"_benchmark_name": "b7", "--re te": "inf"}]),
                encoding="utf-8",
            )
            (config_dir / "vendor_profile.json").write_text(
                json.dumps(
                    {
                        "vendor_name": "xpu",
                        "target_vllm_image": "local/vllm:xpu",
                        "resource_name": "vendor.com/xpu",
                        "resource_count": 1,
                    }
                ),
                encoding="utf-8",
            )
            (config_dir / "model_config.json").write_text(
                json.dumps(
                    {
                        "model_name": "qwen",
                        "model_path": "/models/qwen",
                        "served_model_name": "qwen",
                        "trust_remote_code": True,
                        "dtype": "float16",
                    }
                ),
                encoding="utf-8",
            )

            run_config = load_run_config_from_dir(config_dir, run_id="run-001", namespace="bench")

        self.assertEqual(run_config.serve_configs[0].benchmark_name, "s1")
        self.assertNotIn("--re te", run_config.bench_configs[0].params)
        self.assertEqual(run_config.bench_configs[0].params["--request-rate"], "inf")

    def test_target_pod_and_service_builders_encode_resource_and_endpoint(self):
        from tests.test_backend_submit_job import valid_payload
        from vllm_bench_platform.backend.submit_job import SubmitJobRequest
        from vllm_bench_platform.master.service_builder import (
            build_target_service,
            target_endpoint,
        )
        from vllm_bench_platform.master.target_pod_builder import build_target_pod

        run_config = SubmitJobRequest.from_payload(valid_payload()).run_config
        serve_config = run_config.serve_configs[0]

        pod = build_target_pod(run_config, serve_config)
        service = build_target_service(run_config, serve_config)

        container = pod["spec"]["containers"][0]
        self.assertEqual(container["image"], "registry.local/vllm:xpu")
        self.assertEqual(container["resources"]["requests"]["vendor.com/xpu"], 1)
        self.assertIn("--tensor-parallel-size", container["args"])
        self.assertIn("--pipeline-parallel-size", container["args"])
        env = {item["name"]: item["value"] for item in container["env"]}
        self.assertEqual(env.get("HF_HUB_DISABLE_XET"), "1")
        self.assertIn("--max-num-seqs", container["args"])
        self.assertIn("run-001", service["metadata"]["name"])
        self.assertIn("s1", service["metadata"]["name"])
        self.assertEqual(target_endpoint(run_config, serve_config), f"http://{service['metadata']['name']}:8000")

    def test_controller_resource_planning_applies_before_creating_target_pod(self):
        from vllm_bench_platform.master.master import run_controller

        class CapturingKubernetes:
            def __init__(self):
                self.pod = None

            def create_pod(self, manifest):
                self.pod = manifest

            def create_service(self, manifest): ...
            def wait_pod_ready(self, name, namespace, timeout_seconds=600): return False
            def pod_phase(self, name, namespace): return "Pending"
            def pod_node_name(self, name, namespace): return "node-a"
            def get_pod_logs(self, name, namespace): return ""
            def get_pod_events(self, name, namespace): return ""
            def delete_service(self, name, namespace): ...
            def delete_pod(self, name, namespace): ...
            def wait_pod_deleted(self, name, namespace, timeout_seconds=120): return True

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "configs"
            config_dir.mkdir()
            (config_dir / "serve_hparams.json").write_text(json.dumps([{"_benchmark_name": "s1"}]), encoding="utf-8")
            (config_dir / "bench_hparams.json").write_text(json.dumps([{"_benchmark_name": "b1"}]), encoding="utf-8")
            (config_dir / "vendor_profile.json").write_text(
                json.dumps(
                    {
                        "vendor_name": "xpu",
                        "target_vllm_image": "local/vllm:xpu",
                        "resource_name": "vendor.com/xpu",
                        "resource_count": 1,
                        "tensor_parallel_size": 1,
                        "pipeline_parallel_size": 1,
                    }
                ),
                encoding="utf-8",
            )
            (config_dir / "model_config.json").write_text(
                json.dumps(
                    {
                        "model_name": "org/model",
                        "model_path": "/models/qwen",
                        "served_model_name": "qwen",
                        "trust_remote_code": True,
                        "dtype": "float16",
                    }
                ),
                encoding="utf-8",
            )
            k8s = CapturingKubernetes()

            def fetch_json(url, token):
                if url.endswith("/resolve/main/config.json"):
                    return {"num_attention_heads": 40}
                if "/api/models/" in url:
                    return {"siblings": [{"rfilename": "model.safetensors", "size": 40_000_000_000}]}
                raise AssertionError(url)

            run_controller(
                config_dir=config_dir,
                results_root=root / "results",
                work_dir=root / "work",
                run_id="run-001",
                namespace="bench",
                k8s_client=k8s,
                bench_client=object(),
                release_sleep_seconds=0,
                target_gpu_memory_gb=24,
                resource_metadata_fetcher=fetch_json,
            )

        container = k8s.pod["spec"]["containers"][0]
        self.assertEqual(container["resources"]["requests"]["vendor.com/xpu"], 4)
        args = container["args"]
        self.assertEqual(args[args.index("--tensor-parallel-size") + 1], "4")
        self.assertEqual(args[args.index("--pipeline-parallel-size") + 1], "1")

    def test_controller_records_container_failure_reason_without_waiting_for_timeout(self):
        from vllm_bench_platform.master.master import run_controller

        class FailedKubernetes:
            def create_pod(self, manifest): ...
            def create_service(self, manifest): ...
            def wait_pod_ready(self, name, namespace, timeout_seconds=600): return False
            def pod_phase(self, name, namespace): return "Running"
            def pod_failure_reason(self, name, namespace): return "OOMKilled"
            def pod_node_name(self, name, namespace): return "node-a"
            def get_pod_logs(self, name, namespace): return "oom"
            def get_pod_events(self, name, namespace): return "{}"
            def delete_service(self, name, namespace): ...
            def delete_pod(self, name, namespace): ...
            def wait_pod_deleted(self, name, namespace, timeout_seconds=120): return True

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "configs"
            config_dir.mkdir()
            (config_dir / "serve_hparams.json").write_text(json.dumps([{"_benchmark_name": "s1"}]), encoding="utf-8")
            (config_dir / "bench_hparams.json").write_text(json.dumps([{"_benchmark_name": "b1"}]), encoding="utf-8")
            (config_dir / "vendor_profile.json").write_text(
                json.dumps(
                    {
                        "vendor_name": "xpu",
                        "target_vllm_image": "local/vllm:xpu",
                        "resource_name": "vendor.com/xpu",
                        "resource_count": 1,
                        "tensor_parallel_size": 1,
                        "pipeline_parallel_size": 1,
                    }
                ),
                encoding="utf-8",
            )
            (config_dir / "model_config.json").write_text(
                json.dumps(
                    {
                        "model_name": "org/model",
                        "model_path": "/models/qwen",
                        "served_model_name": "qwen",
                        "trust_remote_code": True,
                        "dtype": "float16",
                    }
                ),
                encoding="utf-8",
            )

            def fetch_json(url, token):
                if url.endswith("/resolve/main/config.json"):
                    return {"num_attention_heads": 16}
                if "/api/models/" in url:
                    return {"siblings": [{"rfilename": "model.safetensors", "size": 1_000_000_000}]}
                raise AssertionError(url)

            run_controller(
                config_dir=config_dir,
                results_root=root / "results",
                work_dir=root / "work",
                run_id="run-001",
                namespace="bench",
                k8s_client=FailedKubernetes(),
                bench_client=object(),
                release_sleep_seconds=0,
                target_gpu_memory_gb=8,
                resource_metadata_fetcher=fetch_json,
            )
            failed = json.loads((root / "results" / "run-001" / "failed_cases.jsonl").read_text(encoding="utf-8"))

        self.assertEqual(failed["error_type"], "TARGET_POD_FAILED")
        self.assertIn("OOMKilled", failed["error_message"])

    def test_target_pod_mounts_model_and_cache_when_configured(self):
        from tests.test_backend_submit_job import valid_payload
        from vllm_bench_platform.backend.submit_job import SubmitJobRequest
        from vllm_bench_platform.master.target_pod_builder import build_target_pod

        payload = valid_payload()
        payload["model_config"].update(
            {
                "model_path": "/models/qwen",
                "model_host_path": "/mnt/models/qwen",
                "model_mount_path": "/models/qwen",
                "model_cache_host_path": "/mnt/cache/hf",
                "model_cache_mount_path": "/cache/huggingface",
            }
        )
        run_config = SubmitJobRequest.from_payload(payload).run_config

        pod = build_target_pod(run_config, run_config.serve_configs[0])

        volumes = {volume["name"]: volume for volume in pod["spec"]["volumes"]}
        self.assertEqual(volumes["model"]["hostPath"]["path"], "/mnt/models/qwen")
        self.assertEqual(volumes["model-cache"]["hostPath"]["type"], "DirectoryOrCreate")
        container = pod["spec"]["containers"][0]
        mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
        self.assertEqual(mounts["model"]["mountPath"], "/models/qwen")
        self.assertTrue(mounts["model"]["readOnly"])
        self.assertEqual(mounts["model-cache"]["mountPath"], "/cache/huggingface")
        env = {item["name"]: item["value"] for item in container["env"]}
        self.assertEqual(env["HF_HOME"], "/cache/huggingface")
        self.assertEqual(env["HUGGINGFACE_HUB_CACHE"], "/cache/huggingface")

    def test_result_writer_creates_layout_summary_failed_and_best_config(self):
        from vllm_bench_platform.master.analyzer import write_best_config
        from vllm_bench_platform.master.result_writer import ResultWriter
        from vllm_bench_platform.schemas import ErrorType

        with tempfile.TemporaryDirectory() as tmp:
            writer = ResultWriter(Path(tmp), "run-001")
            writer.initialize({"namespace": "bench"})
            writer.append_summary(
                {
                    "run_id": "run-001",
                    "case_id": "s1-b1",
                    "serve_config": "s1",
                    "bench_config": "b1",
                    "target_endpoint": "http://target:8000",
                    "attempt": 1,
                    "raw_json_path": "/results/run-001/raw_json/s1-b1.json",
                    "raw_log_path": "/results/run-001/raw_logs/s1-b1.log",
                    "metrics": {"total_token_throughput": 10.0, "e2el_mean_ms": 20.0},
                }
            )
            writer.append_failed_case(
                {
                    "run_id": "run-001",
                    "case_id": "s1-b2",
                    "serve_config": {"_benchmark_name": "s1"},
                    "bench_config": {"_benchmark_name": "b2"},
                    "attempt": 2,
                    "error_type": ErrorType.BENCH_TIMEOUT,
                    "error_message": "timeout",
                    "raw_log_path": "/results/run-001/raw_logs/s1-b2.log",
                    "target_pod_name": "target-run-001-s1",
                    "target_node_name": "node-a",
                    "start_time": "2026-06-26T00:00:00Z",
                    "end_time": "2026-06-26T00:01:00Z",
                }
            )
            best = write_best_config(writer.run_root)
            with (writer.run_root / "summary.csv").open(encoding="utf-8") as handle:
                summary_rows = list(csv.DictReader(handle))
            failed = [
                json.loads(line)
                for line in (writer.run_root / "failed_cases.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(summary_rows[0]["case_id"], "s1-b1")
        self.assertEqual(failed[0]["error_type"], "BENCH_TIMEOUT")
        self.assertTrue(best["has_successful_case"])
        self.assertEqual(best["selected_case"]["case_id"], "s1-b1")


if __name__ == "__main__":
    unittest.main()
