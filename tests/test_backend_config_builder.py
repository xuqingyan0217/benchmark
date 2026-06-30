import json
import unittest

from tests.test_backend_submit_job import valid_payload


class ConfigMapBuilderTest(unittest.TestCase):
    def test_config_map_contains_exact_required_config_keys(self):
        from vllm_bench_platform.backend.config_builder import build_config_map
        from vllm_bench_platform.backend.submit_job import SubmitJobRequest

        request = SubmitJobRequest.from_payload(valid_payload())

        manifest = build_config_map(request.run_config)

        self.assertEqual(manifest["kind"], "ConfigMap")
        self.assertEqual(manifest["metadata"]["name"], "vllm-bench-config-run-001")
        self.assertEqual(manifest["metadata"]["namespace"], "bench")
        self.assertEqual(
            set(manifest["data"]),
            {
                "serve_hparams.json",
                "bench_hparams.json",
                "vendor_profile.json",
                "model_config.json",
            },
        )
        self.assertEqual(
            json.loads(manifest["data"]["serve_hparams.json"])[0]["_benchmark_name"],
            "s1",
        )
        self.assertEqual(
            json.loads(manifest["data"]["bench_hparams.json"])[0]["_benchmark_name"],
            "b1",
        )
        self.assertEqual(
            json.loads(manifest["data"]["vendor_profile.json"])["resource_name"],
            "vendor.com/xpu",
        )
        self.assertEqual(
            json.loads(manifest["data"]["model_config.json"])["model_path"],
            "/models/qwen",
        )


if __name__ == "__main__":
    unittest.main()
