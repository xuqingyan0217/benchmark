import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class MasterComponentsTest(unittest.TestCase):
    def test_bench_runner_client_uses_configured_request_timeout_for_long_benchmarks(self):
        from vllm_bench_platform.master.bench_client import BenchRunnerClient

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"success": true}'

        calls = {}

        def fake_urlopen(request, timeout):
            calls["url"] = request.full_url
            calls["timeout"] = timeout
            calls["body"] = request.data
            return FakeResponse()

        with patch("vllm_bench_platform.master.bench_client.urlopen", fake_urlopen):
            result = BenchRunnerClient(
                base_url="http://127.0.0.1:18080",
                request_timeout_seconds=615,
            ).run_bench({"case_id": "s1-b1"})

        self.assertTrue(result["success"])
        self.assertEqual(calls["url"], "http://127.0.0.1:18080/run-bench")
        self.assertEqual(calls["timeout"], 615)
        self.assertEqual(json.loads(calls["body"].decode("utf-8")), {"case_id": "s1-b1"})

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
        env = {item["name"]: item["value"] for item in container["env"]}
        self.assertEqual(env.get("HF_HUB_DISABLE_XET"), "1")
        self.assertIn("--max-num-seqs", container["args"])
        self.assertIn("run-001", service["metadata"]["name"])
        self.assertIn("s1", service["metadata"]["name"])
        self.assertEqual(target_endpoint(run_config, serve_config), f"http://{service['metadata']['name']}:8000")

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
