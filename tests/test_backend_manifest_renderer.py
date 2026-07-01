import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class BackendManifestRendererTest(unittest.TestCase):
    def test_render_manifests_writes_complete_apply_order(self):
        from tests.test_backend_submit_job import valid_payload
        from vllm_bench_platform.backend.job_builder import MasterJobOptions
        from vllm_bench_platform.backend.manifest_renderer import render_manifests

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "rendered"
            rendered = render_manifests(
                valid_payload(),
                host_path="/tmp/vllm-bench/bench/run-001",
                output_dir=output_dir,
                master_options=MasterJobOptions(
                    master_image="local/master:dev",
                    bench_binary="/usr/local/bin/vllm-bench",
                    bench_timeout_seconds=30,
                    bench_num_prompts=10,
                ),
            )
            names = [path.name for path in rendered.files]
            configmap = (output_dir / "04-configmap.yaml").read_text(encoding="utf-8")
            job = (output_dir / "05-master-job.yaml").read_text(encoding="utf-8")

        self.assertEqual(
            names,
            [
                "00-namespace.yaml",
                "01-rbac.yaml",
                "02-pv.yaml",
                "03-pvc.yaml",
                "04-configmap.yaml",
                "05-master-job.yaml",
            ],
        )
        self.assertEqual(rendered.run_id, "run-001")
        self.assertEqual(rendered.namespace, "bench")
        self.assertIn("kind: ConfigMap", configmap)
        self.assertIn("serve_hparams.json", configmap)
        self.assertIn("kind: Job", job)
        self.assertIn("name: master-controller", job)
        self.assertIn("BENCH_BINARY", job)
        self.assertNotIn("bench-runner", job)

    def test_cli_render_writes_yaml_without_kubectl(self):
        from vllm_bench_platform.backend.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / "enving.env"
            serve_path = root / "serve.json"
            bench_path = root / "bench.json"
            output_dir = root / "yaml"
            env_path.write_text(
                "\n".join(
                    [
                        "NAMESPACE=bench",
                        "MASTER_IMAGE=local/master:dev",
                        "TARGET_VLLM_IMAGE=local/vllm:xpu",
                        "TARGET_RESOURCE_NAME=vendor.com/xpu",
                        "TARGET_RESOURCE_COUNT=1",
                        "TENSOR_PARALLEL_SIZE=1",
                        "PIPELINE_PARALLEL_SIZE=1",
                        "MODEL_PATH=/models/qwen",
                        "SERVED_MODEL_NAME=qwen",
                        "DTYPE=float16",
                        "PERSIST_ROOT=/tmp/vllm-bench",
                        "BENCH_BINARY=vllm-bench",
                        "BENCH_TIMEOUT_SECONDS=30",
                        "BENCH_NUM_PROMPTS=10",
                    ]
                ),
                encoding="utf-8",
            )
            serve_path.write_text(json.dumps([{"_benchmark_name": "s1"}]), encoding="utf-8")
            bench_path.write_text(json.dumps([{"_benchmark_name": "b1"}]), encoding="utf-8")

            output = []
            with patch("builtins.print", lambda value: output.append(value)):
                exit_code = main(
                    [
                        "render",
                        "--env",
                        str(env_path),
                        "--serve-configs",
                        str(serve_path),
                        "--bench-configs",
                        str(bench_path),
                        "--run-id",
                        "run-001",
                        "--output-dir",
                        str(output_dir),
                    ]
                )
            namespace_exists = (output_dir / "00-namespace.yaml").is_file()
            job_exists = (output_dir / "05-master-job.yaml").is_file()
            pv = (output_dir / "02-pv.yaml").read_text(encoding="utf-8")
            job = (output_dir / "05-master-job.yaml").read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output[0])["run_id"], "run-001")
        self.assertTrue(namespace_exists)
        self.assertTrue(job_exists)
        self.assertIn("path: /tmp/vllm-bench/bench/run-001", pv)
        self.assertIn("mountPath: /results/run-001", job)


if __name__ == "__main__":
    unittest.main()
