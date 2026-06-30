import json
from pathlib import Path
import tempfile
import unittest


class BackendRuntimeAndKubectlTest(unittest.TestCase):
    def test_env_file_builds_submit_payload_and_normalizes_reference_typo(self):
        from vllm_bench_platform.backend.runtime_config import (
            build_payload_from_files,
            load_env_config,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / "enving.env"
            env_path.write_text(
                "\n".join(
                    [
                        "NAMESPACE=bench",
                        "MASTER_IMAGE=local/master:dev",
                        "BENCH_RUNNER_IMAGE=local/bench:dev",
                        "TARGET_VLLM_IMAGE=local/vllm:xpu",
                        "TARGET_RESOURCE_NAME=vendor.com/xpu",
                        "TARGET_RESOURCE_COUNT=2",
                        "MODEL_PATH=/models/qwen",
                        "SERVED_MODEL_NAME=qwen",
                        "DTYPE=float16",
                        "RESULTS_HOST_PATH=/tmp/vllm-bench-results",
                        "BENCH_COMMAND=vllm bench serve",
                        "BENCH_TIMEOUT_SECONDS=30",
                        "BENCH_NUM_PROMPTS=10",
                        "BENCH_RUNNER_HEALTH_TIMEOUT_SECONDS=300",
                        "BENCH_RUNNER_REQUEST_TIMEOUT_SECONDS=660",
                        "MASTER_MEMORY_REQUEST=256Mi",
                        "MASTER_MEMORY_LIMIT=512Mi",
                        "BENCH_RUNNER_MEMORY_REQUEST=1Gi",
                        "BENCH_RUNNER_MEMORY_LIMIT=2Gi",
                        "TARGET_ENV_JSON={\"HF_HUB_DISABLE_XET\":\"1\"}",
                        "POD_TOLERATIONS_JSON=[{\"key\":\"node-role.kubernetes.io/control-plane\",\"operator\":\"Exists\",\"effect\":\"NoSchedule\"}]",
                    ]
                ),
                encoding="utf-8",
            )
            serve_path = root / "serve.json"
            serve_path.write_text(
                json.dumps(
                    [
                        {
                            "_benchmark_name": "s1",
                            "--max-num-seqs": 8,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            bench_path = root / "bench.json"
            bench_path.write_text(
                json.dumps(
                    [
                        {
                            "_benchmark_name": "b7",
                            "--random-input-len": 1024,
                            "--re te": "inf",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            env = load_env_config(env_path)
            payload = build_payload_from_files(env, serve_path, bench_path, run_id="run-001")

        self.assertEqual(env.namespace, "bench")
        self.assertEqual(env.bench_runner_health_timeout_seconds, 300)
        self.assertEqual(env.bench_runner_request_timeout_seconds, 660)
        self.assertEqual(env.bench_runner_memory_limit, "2Gi")
        self.assertEqual(payload["namespace"], "bench")
        self.assertEqual(payload["vendor_profile"]["target_vllm_image"], "local/vllm:xpu")
        self.assertEqual(payload["vendor_profile"]["resource_count"], 2)
        self.assertEqual(payload["vendor_profile"]["env"]["HF_HUB_DISABLE_XET"], "1")
        self.assertEqual(
            payload["vendor_profile"]["tolerations"],
            [{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists", "effect": "NoSchedule"}],
        )
        self.assertEqual(payload["model_config"]["model_path"], "/models/qwen")
        self.assertNotIn("--re te", payload["bench_hparams"][0])
        self.assertEqual(payload["bench_hparams"][0]["--request-rate"], "inf")

    def test_job_prerequisites_and_master_images_are_configurable(self):
        from vllm_bench_platform.backend.job_builder import (
            MasterJobOptions,
            build_master_job,
            build_namespace,
            build_rbac_manifests,
            build_results_pv,
        )
        from vllm_bench_platform.backend.submit_job import SubmitJobRequest
        from tests.test_backend_submit_job import valid_payload

        run_config = SubmitJobRequest.from_payload(valid_payload()).run_config
        namespace = build_namespace("bench")
        pv = build_results_pv(run_config, host_path="/tmp/vllm-bench-results")
        rbac = build_rbac_manifests("bench")
        job = build_master_job(
            run_config,
            MasterJobOptions(
                master_image="local/master:dev",
                bench_runner_image="local/bench:dev",
                bench_command="vllm bench serve",
                bench_timeout_seconds=30,
                bench_num_prompts=10,
                bench_runner_health_timeout_seconds=300,
                bench_runner_request_timeout_seconds=660,
                master_memory_request="256Mi",
                master_memory_limit="512Mi",
                bench_runner_memory_request="1Gi",
                bench_runner_memory_limit="2Gi",
                pod_tolerations=[
                    {
                        "key": "node-role.kubernetes.io/control-plane",
                        "operator": "Exists",
                        "effect": "NoSchedule",
                    }
                ],
            ),
        )

        self.assertEqual(namespace["kind"], "Namespace")
        self.assertEqual(pv["kind"], "PersistentVolume")
        self.assertEqual(pv["spec"]["hostPath"]["path"], "/tmp/vllm-bench-results")
        role = next(item for item in rbac if item["kind"] == "Role")
        role_resources = {tuple(rule["resources"]) for rule in role["rules"]}
        self.assertIn(("pods",), role_resources)
        self.assertIn(("pods/log",), role_resources)
        self.assertIn(("events",), role_resources)
        containers = {
            container["name"]: container
            for container in job["spec"]["template"]["spec"]["containers"]
        }
        self.assertEqual(containers["master-controller"]["image"], "local/master:dev")
        self.assertEqual(containers["bench-runner"]["image"], "local/bench:dev")
        self.assertEqual(containers["bench-runner"]["resources"]["requests"]["memory"], "1Gi")
        self.assertEqual(containers["bench-runner"]["resources"]["limits"]["memory"], "2Gi")
        bench_env = {
            item["name"]: item["value"]
            for item in containers["bench-runner"]["env"]
        }
        self.assertEqual(bench_env["BENCH_NUM_PROMPTS"], "10")
        master_env = {
            item["name"]: item["value"]
            for item in containers["master-controller"]["env"]
        }
        self.assertEqual(master_env["BENCH_RUNNER_HEALTH_TIMEOUT_SECONDS"], "300")
        self.assertEqual(master_env["BENCH_RUNNER_REQUEST_TIMEOUT_SECONDS"], "660")
        self.assertIn("python3", containers["master-controller"]["command"])
        self.assertEqual(
            job["spec"]["template"]["spec"]["tolerations"],
            [{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists", "effect": "NoSchedule"}],
        )

    def test_kubectl_submit_client_applies_json_manifests(self):
        from vllm_bench_platform.backend.kubectl_client import KubectlSubmitClient

        calls = []

        def runner(args, input_text=None, timeout=None):
            calls.append((args, json.loads(input_text), timeout))
            return ""

        client = KubectlSubmitClient(runner=runner)
        client.apply_manifest({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "bench"}})

        self.assertEqual(calls[0][0], ["kubectl", "apply", "-f", "-"])
        self.assertEqual(calls[0][1]["kind"], "Namespace")

    def test_query_helpers_read_status_results_and_failed_cases(self):
        from vllm_bench_platform.backend.query import (
            get_run_status,
            list_result_files,
            read_failed_cases,
        )

        commands = []

        def runner(args, input_text=None, timeout=None):
            commands.append(args)
            if args[:3] == ["kubectl", "get", "job"]:
                return json.dumps(
                    {
                        "metadata": {"name": "vllm-bench-master-run-001"},
                        "status": {"succeeded": 1, "startTime": "2026-06-26T00:00:00Z"},
                    }
                )
            return "{}"

        with tempfile.TemporaryDirectory() as tmp:
            result_root = Path(tmp)
            run_root = result_root / "run-001"
            (run_root / "raw_logs").mkdir(parents=True)
            (run_root / "summary.csv").write_text("header\n", encoding="utf-8")
            (run_root / "failed_cases.jsonl").write_text(
                json.dumps({"case_id": "s1-b1", "error_type": "BENCH_TIMEOUT"}) + "\n",
                encoding="utf-8",
            )

            status = get_run_status("run-001", "bench", result_root, runner=runner)
            files = list_result_files("run-001", result_root)
            failed = read_failed_cases("run-001", result_root)

        self.assertEqual(status["status"], "Succeeded")
        self.assertEqual(status["master_job_name"], "vllm-bench-master-run-001")
        self.assertIn("summary.csv", files)
        self.assertEqual(failed[0]["case_id"], "s1-b1")


if __name__ == "__main__":
    unittest.main()
