import unittest

from tests.test_backend_submit_job import valid_payload


class JobBuilderTest(unittest.TestCase):
    def test_pvc_defaults_to_read_write_once(self):
        from vllm_bench_platform.backend.job_builder import build_results_pvc
        from vllm_bench_platform.backend.submit_job import SubmitJobRequest

        run_config = SubmitJobRequest.from_payload(valid_payload()).run_config

        manifest = build_results_pvc(run_config)

        self.assertEqual(manifest["kind"], "PersistentVolumeClaim")
        self.assertEqual(manifest["metadata"]["name"], "vllm-bench-results-run-001")
        self.assertEqual(manifest["spec"]["accessModes"], ["ReadWriteOnce"])

    def test_master_job_has_single_container_without_accelerator_resources(self):
        from vllm_bench_platform.backend.job_builder import build_master_job
        from vllm_bench_platform.backend.submit_job import SubmitJobRequest

        run_config = SubmitJobRequest.from_payload(valid_payload()).run_config

        manifest = build_master_job(run_config)
        pod_spec = manifest["spec"]["template"]["spec"]
        containers = pod_spec["containers"]
        container_names = {container["name"] for container in containers}

        self.assertEqual(manifest["kind"], "Job")
        self.assertEqual(container_names, {"master-controller"})
        for container in containers:
            resources = container.get("resources", {})
            requests = resources.get("requests", {})
            limits = resources.get("limits", {})
            self.assertNotIn("vendor.com/xpu", requests)
            self.assertNotIn("vendor.com/xpu", limits)

    def test_master_job_mounts_configs_results_and_work(self):
        from vllm_bench_platform.backend.job_builder import build_master_job
        from vllm_bench_platform.backend.submit_job import SubmitJobRequest

        run_config = SubmitJobRequest.from_payload(valid_payload()).run_config

        manifest = build_master_job(run_config)
        pod_spec = manifest["spec"]["template"]["spec"]
        volume_names = {volume["name"] for volume in pod_spec["volumes"]}

        self.assertEqual(volume_names, {"configs", "results", "work"})
        container = pod_spec["containers"][0]
        mount_paths = {
            mount["name"]: mount["mountPath"]
            for mount in container["volumeMounts"]
        }
        self.assertEqual(mount_paths["configs"], "/configs")
        self.assertEqual(mount_paths["results"], "/results/run-001")
        self.assertEqual(mount_paths["work"], "/work")

    def test_master_job_only_exposes_controller_runtime_env(self):
        from vllm_bench_platform.backend.job_builder import build_master_job
        from vllm_bench_platform.backend.submit_job import SubmitJobRequest

        run_config = SubmitJobRequest.from_payload(valid_payload()).run_config

        manifest = build_master_job(run_config)

        pod_spec = manifest["spec"]["template"]["spec"]
        volume_names = {volume["name"] for volume in pod_spec["volumes"]}
        self.assertEqual(volume_names, {"configs", "results", "work"})
        env = {
            item["name"]: item["value"]
            for item in pod_spec["containers"][0]["env"]
        }
        self.assertEqual(set(env), {"RUN_ID", "NAMESPACE", "BENCH_BINARY", "BENCH_TIMEOUT_SECONDS", "BENCH_NUM_PROMPTS"})


if __name__ == "__main__":
    unittest.main()
