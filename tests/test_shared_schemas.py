import unittest


class SharedSchemasTest(unittest.TestCase):
    def test_valid_run_config_accepts_single_mvp_scope(self):
        from vllm_bench_platform.schemas import (
            BenchConfig,
            ModelConfig,
            RunConfig,
            ServeConfig,
            VendorProfile,
        )

        run = RunConfig(
            run_id="run-001",
            namespace="bench",
            serve_configs=[
                ServeConfig(
                    _benchmark_name="s4_low_concurrency",
                    params={
                        "--max-num-seqs": 8,
                        "--max-num-batched-tokens": 16384,
                    },
                )
            ],
            bench_configs=[
                BenchConfig(
                    _benchmark_name="b3_short_prompt_high_qps",
                    params={
                        "--random-input-len": 512,
                        "--random-output-len": 128,
                        "--request-rate": 16,
                    },
                )
            ],
            vendor_profile=VendorProfile(
                vendor_name="xpu",
                target_vllm_image="registry.local/vllm:xpu",
                resource_name="vendor.com/xpu",
                resource_count=1,
                env={"ENV_NAME": "value"},
                node_selector={"accelerator": "xpu"},
                tolerations=[],
                runtime_class_name="xpu-runtime",
                shm_size="16Gi",
                port=8000,
                health_path="/health",
                extra_serve_args=["--disable-log-requests"],
            ),
            model_config=ModelConfig(
                model_name="qwen",
                model_path="/models/qwen",
                served_model_name="qwen",
                trust_remote_code=True,
                dtype="float16",
            ),
        )

        self.assertEqual(run.serve_configs[0].benchmark_name, "s4_low_concurrency")
        self.assertEqual(run.bench_configs[0].benchmark_name, "b3_short_prompt_high_qps")
        self.assertEqual(run.vendor_profile.resource_name, "vendor.com/xpu")

    def test_matrix_entry_requires_benchmark_name(self):
        from vllm_bench_platform.schemas import ServeConfig, ValidationError

        with self.assertRaisesRegex(ValidationError, "_benchmark_name"):
            ServeConfig(_benchmark_name="", params={"--max-num-seqs": 8})

    def test_run_config_rejects_unsupported_mvp_fan_out(self):
        from vllm_bench_platform.schemas import (
            BenchConfig,
            ModelConfig,
            RunConfig,
            ServeConfig,
            ValidationError,
            VendorProfile,
        )

        with self.assertRaisesRegex(ValidationError, "单集群"):
            RunConfig(
                run_id="run-001",
                namespace="bench",
                clusters=["cluster-a", "cluster-b"],
                serve_configs=[ServeConfig(_benchmark_name="s1", params={})],
                bench_configs=[BenchConfig(_benchmark_name="b1", params={})],
                vendor_profile=VendorProfile(
                    vendor_name="xpu",
                    target_vllm_image="registry.local/vllm:xpu",
                    resource_name="vendor.com/xpu",
                    resource_count=1,
                    env={},
                    node_selector={},
                    tolerations=[],
                    runtime_class_name=None,
                    shm_size="16Gi",
                    port=8000,
                    health_path="/health",
                    extra_serve_args=[],
                ),
                model_config=ModelConfig(
                    model_name="qwen",
                    model_path="/models/qwen",
                    served_model_name="qwen",
                    trust_remote_code=True,
                    dtype="float16",
                ),
            )

    def test_failed_case_schema_requires_standard_error_type_and_fields(self):
        from vllm_bench_platform.schemas import ErrorType, FailedCase, ValidationError

        case = FailedCase(
            run_id="run-001",
            case_id="s1-b1-a2",
            serve_config={"_benchmark_name": "s1"},
            bench_config={"_benchmark_name": "b1"},
            attempt=2,
            error_type=ErrorType.BENCH_TIMEOUT,
            error_message="benchmark timed out",
            raw_log_path="/results/run-001/raw_logs/s1-b1.log",
            target_pod_name="target-run-001-s1",
            target_node_name="node-a",
            start_time="2026-06-26T10:00:00Z",
            end_time="2026-06-26T10:10:00Z",
        )

        self.assertEqual(case.error_type, ErrorType.BENCH_TIMEOUT)

        with self.assertRaisesRegex(ValidationError, "error_type"):
            FailedCase(
                run_id="run-001",
                case_id="s1-b1-a2",
                serve_config={"_benchmark_name": "s1"},
                bench_config={"_benchmark_name": "b1"},
                attempt=2,
                error_type="NOT_A_STANDARD_ERROR",
                error_message="bad type",
                raw_log_path="/results/run-001/raw_logs/s1-b1.log",
                target_pod_name="target-run-001-s1",
                target_node_name="node-a",
                start_time="2026-06-26T10:00:00Z",
                end_time="2026-06-26T10:10:00Z",
            )


if __name__ == "__main__":
    unittest.main()
