import json
from pathlib import Path
import tempfile
import unittest


class CompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


SAMPLE_VLLM_OUTPUT = """
Successful requests:                     500
Benchmark duration (s):                  503.90
Total input tokens:                      255169
Total generated tokens:                  64000
Traffic request rate:                    1.0
Mean TTFT (ms):                          124.32
P99 TTFT (ms):                           177.15
Mean TPOT (ms):                          43.06
P99 TPOT (ms):                           56.18
Mean ITL (ms):                           43.06
P99 ITL (ms):                            93.49
Mean E2EL (ms):                          5593.34
P99 E2EL (ms):                           7255.46
Total Token throughput (tok/s):          633.39
"""


class MasterBenchRunnerTest(unittest.TestCase):
    def test_parser_extracts_reference_metrics(self):
        from vllm_bench_platform.master.result_parser import parse_bench_metrics

        metrics = parse_bench_metrics({}, SAMPLE_VLLM_OUTPUT)

        self.assertEqual(metrics["successful_requests"], 500)
        self.assertEqual(metrics["ttft_mean_ms"], 124.32)
        self.assertEqual(metrics["e2el_p99_ms"], 7255.46)
        self.assertEqual(metrics["total_token_throughput"], 633.39)

    def test_parser_prefers_json_metrics(self):
        from vllm_bench_platform.master.result_parser import parse_bench_metrics

        metrics = parse_bench_metrics(
            {
                "successful_requests": 10,
                "metrics": {
                    "total_token_throughput": 99.5,
                    "mean_ttft_ms": 7.5,
                },
            },
            SAMPLE_VLLM_OUTPUT,
        )

        self.assertEqual(metrics["successful_requests"], 10)
        self.assertEqual(metrics["ttft_mean_ms"], 7.5)
        self.assertEqual(metrics["total_token_throughput"], 99.5)

    def test_command_builder_normalizes_reference_typo_and_uses_service_endpoint(self):
        from vllm_bench_platform.master.bench_runner import (
            BenchRunRequest,
            build_vllm_bench_command,
            is_localhost_endpoint,
        )

        request = BenchRunRequest(
            target_endpoint="http://vllm-target-run-s1:8000",
            run_id="run-001",
            serve_benchmark_name="s1",
            bench_benchmark_name="b7",
            bench_params={"--random-input-len": 1024, "--re te": "inf"},
            model_path="/models/qwen",
            served_model_name="qwen",
        )

        command = build_vllm_bench_command(
            request,
            bench_binary="vllm-bench",
            num_prompts=10,
            result_dir="/results/run-001/raw_json",
            result_filename="s1-b7.json",
        )

        self.assertFalse(is_localhost_endpoint(request.target_endpoint))
        self.assertTrue(is_localhost_endpoint("http://127.0.0.1:8000"))
        self.assertEqual(command[0], "vllm-bench")
        self.assertIn("--backend", command)
        self.assertEqual(command[command.index("--backend") + 1], "openai")
        self.assertIn("--base-url", command)
        self.assertIn("http://vllm-target-run-s1:8000", command)
        self.assertEqual(command[command.index("--model") + 1], "/models/qwen")
        self.assertEqual(command[command.index("--served-model-name") + 1], "qwen")
        self.assertIn("--ignore-eos", command)
        self.assertIn("--request-rate", command)
        self.assertNotIn("--re te", command)
        self.assertIn("--save-result", command)
        self.assertIn("--result-filename", command)

    def test_run_bench_case_writes_raw_outputs_and_structured_result(self):
        from vllm_bench_platform.master.bench_runner import (
            BenchRunRequest,
            run_bench_case,
        )

        def runner(args, timeout=None, cwd=None, capture_output=None, text=None):
            self.assertIn("vllm-bench", args)
            self.assertEqual(timeout, 30)
            self.assertTrue(capture_output)
            self.assertTrue(text)
            result_path = Path(args[args.index("--result-dir") + 1]) / args[args.index("--result-filename") + 1]
            result_path.write_text(
                json.dumps(
                    {
                        "successful_requests": 500,
                        "metrics": {
                            "mean_ttft_ms": 124.32,
                            "total_token_throughput": 633.39,
                        },
                    }
                ),
                encoding="utf-8",
            )
            return CompletedProcess(returncode=0, stdout=SAMPLE_VLLM_OUTPUT, stderr="stderr text")

        with tempfile.TemporaryDirectory() as tmp:
            result = run_bench_case(
                BenchRunRequest(
                    target_endpoint="http://vllm-target-run-s1:8000",
                    run_id="run-001",
                    serve_benchmark_name="s1",
                    bench_benchmark_name="b1",
                    bench_params={"--request-rate": 1},
                    model_path="/models/qwen",
                    served_model_name="qwen",
                ),
                results_root=Path(tmp) / "results",
                work_dir=Path(tmp) / "work",
                bench_binary="vllm-bench",
                timeout_seconds=30,
                num_prompts=10,
                runner=runner,
            )

            raw_log = Path(result["raw_log_path"])
            raw_json = Path(result["raw_json_path"])
            raw_log_text = raw_log.read_text(encoding="utf-8")
            raw_json_payload = json.loads(raw_json.read_text(encoding="utf-8"))

        self.assertTrue(result["success"])
        self.assertEqual(result["metrics"]["successful_requests"], 500)
        self.assertTrue(raw_log.name.endswith(".log"))
        self.assertTrue(raw_json.name.endswith(".json"))
        self.assertIn("stderr text", raw_log_text)
        self.assertEqual(raw_json_payload["metrics"]["ttft_mean_ms"], 124.32)
        self.assertIn("vllm_bench_result", raw_json_payload)

    def test_localhost_target_returns_failure_without_running_command(self):
        from vllm_bench_platform.master.bench_runner import (
            BenchRunRequest,
            run_bench_case,
        )

        called = False

        def runner(args, timeout=None, cwd=None):
            nonlocal called
            called = True
            return CompletedProcess()

        with tempfile.TemporaryDirectory() as tmp:
            result = run_bench_case(
                BenchRunRequest(
                    target_endpoint="http://localhost:8000",
                    run_id="run-001",
                    serve_benchmark_name="s1",
                    bench_benchmark_name="b1",
                    bench_params={},
                    model_path="/models/qwen",
                    served_model_name="qwen",
                ),
                results_root=Path(tmp) / "results",
                work_dir=Path(tmp) / "work",
                runner=runner,
            )

        self.assertFalse(called)
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "BENCH_COMMAND_FAILED")


if __name__ == "__main__":
    unittest.main()
