import unittest


class RecordingKubernetesClient:
    def __init__(self):
        self.created = []

    def create_config_map(self, manifest):
        self.created.append(("configmap", manifest))

    def create_pvc(self, manifest):
        self.created.append(("pvc", manifest))

    def create_job(self, manifest):
        self.created.append(("job", manifest))


def valid_payload():
    return {
        "run_id": "run-001",
        "namespace": "bench",
        "serve_hparams": [
            {
                "_benchmark_name": "s1",
                "--max-num-seqs": 8,
            }
        ],
        "bench_hparams": [
            {
                "_benchmark_name": "b1",
                "--request-rate": 16,
            }
        ],
        "vendor_profile": {
            "vendor_name": "xpu",
            "target_vllm_image": "registry.local/vllm:xpu",
            "resource_name": "vendor.com/xpu",
            "resource_count": 1,
            "env": {"HF_HUB_DISABLE_XET": "1"},
            "node_selector": {},
            "tolerations": [],
            "runtime_class_name": None,
            "shm_size": "16Gi",
            "port": 8000,
            "health_path": "/health",
            "extra_serve_args": [],
            "tensor_parallel_size": 1,
            "pipeline_parallel_size": 1,
        },
        "model_config": {
            "model_name": "qwen",
            "model_path": "/models/qwen",
            "served_model_name": "qwen",
            "trust_remote_code": True,
            "dtype": "float16",
        },
    }


class SubmitJobTest(unittest.TestCase):
    def test_submit_request_parses_to_run_config(self):
        from vllm_bench_platform.backend.submit_job import SubmitJobRequest

        request = SubmitJobRequest.from_payload(valid_payload())

        self.assertEqual(request.run_config.run_id, "run-001")
        self.assertEqual(request.run_config.namespace, "bench")
        self.assertEqual(request.run_config.serve_configs[0].benchmark_name, "s1")

    def test_invalid_submit_does_not_create_kubernetes_resources(self):
        from vllm_bench_platform.backend.submit_job import submit_run
        from vllm_bench_platform.schemas import ValidationError

        payload = valid_payload()
        payload["bench_hparams"] = [{"--request-rate": 16}]
        client = RecordingKubernetesClient()

        with self.assertRaisesRegex(ValidationError, "_benchmark_name"):
            submit_run(payload, client)

        self.assertEqual(client.created, [])

    def test_submit_run_creates_resources_in_order_and_returns_identity(self):
        from vllm_bench_platform.backend.submit_job import submit_run

        client = RecordingKubernetesClient()

        response = submit_run(valid_payload(), client)

        self.assertEqual(
            [kind for kind, _manifest in client.created],
            ["configmap", "pvc", "job"],
        )
        self.assertEqual(response.run_id, "run-001")
        self.assertEqual(response.namespace, "bench")
        self.assertEqual(response.config_map_name, "vllm-bench-config-run-001")
        self.assertEqual(response.pvc_name, "vllm-bench-results-run-001")
        self.assertEqual(response.master_job_name, "vllm-bench-master-run-001")


if __name__ == "__main__":
    unittest.main()
