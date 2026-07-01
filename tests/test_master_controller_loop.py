from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch


class FakeKubernetesClient:
    def __init__(self):
        self.created_pods = []
        self.created_services = []
        self.deleted_pods = []
        self.deleted_services = []
        self.phase_after_ready_timeout = "Pending"

    def create_pod(self, manifest):
        self.created_pods.append(manifest["metadata"]["name"])

    def create_service(self, manifest):
        self.created_services.append(manifest["metadata"]["name"])

    def wait_pod_ready(self, name, namespace, timeout_seconds=600):
        return True

    def pod_phase(self, name, namespace):
        return self.phase_after_ready_timeout

    def wait_http_ready(self, url, timeout_seconds=600):
        return True

    def pod_node_name(self, name, namespace):
        return "node-a"

    def get_pod_logs(self, name, namespace):
        return f"logs for {name}"

    def get_pod_events(self, name, namespace):
        return f"events for {name}"

    def delete_service(self, name, namespace):
        self.deleted_services.append(name)

    def delete_pod(self, name, namespace):
        self.deleted_pods.append(name)

    def wait_pod_deleted(self, name, namespace, timeout_seconds=120):
        return True


class FakeBenchClient:
    def __init__(self):
        self.calls = []

    def run_bench(self, request):
        self.calls.append(request)
        if request["bench_benchmark_name"] == "b2" and len([c for c in self.calls if c["bench_benchmark_name"] == "b2"]) == 1:
            return {
                "success": False,
                "error_type": "BENCH_COMMAND_FAILED",
                "error_message": "first attempt failed",
                "raw_log_path": "/results/run-001/raw_logs/s1-b2-a1.log",
            }
        return {
            "success": True,
            "exit_code": 0,
            "raw_json_path": f"/results/run-001/raw_json/{request['case_id']}.json",
            "raw_log_path": f"/results/run-001/raw_logs/{request['case_id']}.log",
                "metrics": {
                    "successful_requests": 10,
                    "total_token_throughput": 100.0,
                    "e2el_mean_ms": 10.0,
                },
            }


def fake_hf_fetcher(total_size=1_000_000_000, heads=16):
    def fetch_json(url, token):
        if url.endswith("/resolve/main/config.json"):
            return {"num_attention_heads": heads}
        if "/api/models/" in url:
            return {"siblings": [{"rfilename": "model.safetensors", "size": total_size}]}
        raise AssertionError(url)

    return fetch_json


class MasterControllerLoopTest(unittest.TestCase):
    def test_controller_reuses_target_per_serve_retries_and_writes_outputs(self):
        from vllm_bench_platform.master.master import run_controller

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "configs"
            config_dir.mkdir()
            (config_dir / "serve_hparams.json").write_text(
                json.dumps(
                    [
                        {"_benchmark_name": "s1", "--max-num-seqs": 8},
                        {"_benchmark_name": "s2", "--max-num-seqs": 16},
                    ]
                ),
                encoding="utf-8",
            )
            (config_dir / "bench_hparams.json").write_text(
                json.dumps(
                    [
                        {"_benchmark_name": "b1", "--request-rate": 1},
                        {"_benchmark_name": "b2", "--request-rate": 4},
                    ]
                ),
                encoding="utf-8",
            )
            (config_dir / "vendor_profile.json").write_text(
                json.dumps(
                    {
                        "vendor_name": "xpu",
                        "target_vllm_image": "local/vllm:xpu",
                        "resource_name": "vendor.com/xpu",
                        "resource_count": 1,
                        "port": 8000,
                        "health_path": "/health",
                        "tensor_parallel_size": 1,
                        "pipeline_parallel_size": 1,
                    }
                ),
                encoding="utf-8",
            )
            (config_dir / "model_config.json").write_text(
                json.dumps(
                    {
                        "model_name": "org/qwen",
                        "model_path": "/models/qwen",
                        "served_model_name": "qwen",
                        "trust_remote_code": True,
                        "dtype": "float16",
                    }
                ),
                encoding="utf-8",
            )
            k8s = FakeKubernetesClient()
            bench = FakeBenchClient()

            run_controller(
                config_dir=config_dir,
                results_root=root / "results",
                work_dir=root / "work",
                run_id="run-001",
                namespace="bench",
                k8s_client=k8s,
                bench_client=bench,
                release_sleep_seconds=0,
                target_gpu_memory_gb=8,
                resource_metadata_fetcher=fake_hf_fetcher(),
            )

            summary = (root / "results" / "run-001" / "summary.jsonl").read_text(encoding="utf-8").splitlines()
            best_config = json.loads((root / "results" / "run-001" / "best_config.json").read_text(encoding="utf-8"))

        self.assertEqual(len(k8s.created_pods), 2)
        self.assertEqual(len(k8s.created_services), 2)
        self.assertEqual(len(bench.calls), 5)
        self.assertEqual(len(summary), 4)
        self.assertTrue(best_config["has_successful_case"])
        self.assertEqual(k8s.deleted_pods, k8s.created_pods)

    def test_controller_records_target_pod_failed_when_container_exits_before_ready(self):
        from vllm_bench_platform.master.master import run_controller

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
                        "port": 8000,
                        "health_path": "/health",
                        "tensor_parallel_size": 1,
                        "pipeline_parallel_size": 1,
                    }
                ),
                encoding="utf-8",
            )
            (config_dir / "model_config.json").write_text(
                json.dumps(
                    {
                        "model_name": "org/qwen",
                        "model_path": "/models/qwen",
                        "served_model_name": "qwen",
                        "trust_remote_code": True,
                        "dtype": "float16",
                    }
                ),
                encoding="utf-8",
            )
            k8s = FakeKubernetesClient()
            k8s.phase_after_ready_timeout = "Failed"
            k8s.wait_pod_ready = lambda name, namespace, timeout_seconds=600: False
            bench = FakeBenchClient()

            run_controller(
                config_dir=config_dir,
                results_root=root / "results",
                work_dir=root / "work",
                run_id="run-001",
                namespace="bench",
                k8s_client=k8s,
                bench_client=bench,
                release_sleep_seconds=0,
                target_gpu_memory_gb=8,
                resource_metadata_fetcher=fake_hf_fetcher(),
            )

            failed = [
                json.loads(line)
                for line in (root / "results" / "run-001" / "failed_cases.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(failed[0]["error_type"], "TARGET_POD_FAILED")
        self.assertEqual(failed[0]["case_id"], "s1-b1")

    def test_controller_configures_direct_bench_runner_from_arguments(self):
        from vllm_bench_platform.master.master import run_controller

        created_options = []

        class CapturingBenchRunner(FakeBenchClient):
            def __init__(
                self,
                *,
                results_root,
                work_dir,
                bench_binary="vllm-bench",
                timeout_seconds=1800,
                num_prompts=10,
                process_runner=None,
            ):
                super().__init__()
                created_options.append(
                    {
                        "results_root": str(results_root),
                        "work_dir": str(work_dir),
                        "bench_binary": bench_binary,
                        "timeout_seconds": timeout_seconds,
                        "num_prompts": num_prompts,
                    }
                )

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
                        "port": 8000,
                        "health_path": "/health",
                        "tensor_parallel_size": 1,
                        "pipeline_parallel_size": 1,
                    }
                ),
                encoding="utf-8",
            )
            (config_dir / "model_config.json").write_text(
                json.dumps(
                    {
                        "model_name": "org/qwen",
                        "model_path": "/models/qwen",
                        "served_model_name": "qwen",
                        "trust_remote_code": True,
                        "dtype": "float16",
                    }
                ),
                encoding="utf-8",
            )

            with patch("vllm_bench_platform.master.master.DirectBenchRunner", CapturingBenchRunner):
                run_controller(
                    config_dir=config_dir,
                    results_root=root / "results",
                    work_dir=root / "work",
                    run_id="run-001",
                    namespace="bench",
                    k8s_client=FakeKubernetesClient(),
                    release_sleep_seconds=0,
                    bench_binary="/usr/local/bin/vllm-bench",
                    bench_timeout_seconds=660,
                    bench_num_prompts=20,
                    target_gpu_memory_gb=8,
                    resource_metadata_fetcher=fake_hf_fetcher(),
                )

        self.assertEqual(created_options[0]["bench_binary"], "/usr/local/bin/vllm-bench")
        self.assertEqual(created_options[0]["timeout_seconds"], 660)
        self.assertEqual(created_options[0]["num_prompts"], 20)

    def test_controller_writes_run_error_when_resource_planning_fails(self):
        from vllm_bench_platform.master.master import run_controller
        from vllm_bench_platform.resource_planner import ResourcePlanningError

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
                        "port": 8000,
                        "health_path": "/health",
                        "tensor_parallel_size": 1,
                        "pipeline_parallel_size": 1,
                    }
                ),
                encoding="utf-8",
            )
            (config_dir / "model_config.json").write_text(
                json.dumps(
                    {
                        "model_name": "org/qwen",
                        "model_path": "org/qwen",
                        "served_model_name": "qwen",
                        "trust_remote_code": True,
                        "dtype": "float16",
                    }
                ),
                encoding="utf-8",
            )

            def fetch_json(url, token):
                raise ResourcePlanningError("temporary hf failure")

            with self.assertRaises(ResourcePlanningError):
                run_controller(
                    config_dir=config_dir,
                    results_root=root / "results",
                    work_dir=root / "work",
                    run_id="run-001",
                    namespace="bench",
                    k8s_client=FakeKubernetesClient(),
                    release_sleep_seconds=0,
                    target_gpu_memory_gb=8,
                    resource_metadata_fetcher=fetch_json,
                )

            errors = [
                json.loads(line)
                for line in (root / "results" / "run-001" / "run_errors.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(errors[0]["error_type"], "RESOURCE_PLANNING_FAILED")
        self.assertIn("temporary hf failure", errors[0]["error_message"])


if __name__ == "__main__":
    unittest.main()
