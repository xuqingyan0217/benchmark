from pathlib import Path
import json
import unittest

from vllm_bench_platform.backend.runtime_config import build_payload_from_files, load_env_config


ROOT = Path(__file__).resolve().parents[1]


class ConfigsAndManifestsTest(unittest.TestCase):
    def test_smoke_configs_and_runtime_examples_exist(self):
        expected = [
            ROOT / "configs" / "enving.example.env",
            ROOT / "configs" / "serve_hparams.json",
            ROOT / "configs" / "bench_hparams.json",
            ROOT / "configs" / "serve_hparams.smoke.json",
            ROOT / "configs" / "bench_hparams.smoke.json",
        ]

        for path in expected:
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

    def test_render_path_and_dockerfile_exist_for_smoke_deploy(self):
        from vllm_bench_platform.backend.manifest_renderer import DEFAULT_RENDER_ROOT

        expected = [
            ROOT / "docker" / "Dockerfile.master",
        ]

        for path in expected:
            with self.subTest(path=path):
                self.assertTrue(path.is_file())
        self.assertEqual(DEFAULT_RENDER_ROOT, Path("manifests") / "generated")

    def test_example_env_defaults_fit_local_rtx4060_smoke(self):
        config = load_env_config(ROOT / "configs" / "enving.example.env")

        self.assertEqual(config.master_image, "vllm-bench-platform/master:local")
        self.assertEqual(config.target_vllm_image, "vllm/vllm-openai:v0.8.5")
        self.assertEqual(config.target_resource_name, "nvidia.com/gpu")
        self.assertEqual(config.target_resource_count, 1)
        self.assertEqual(config.tensor_parallel_size, 1)
        self.assertEqual(config.pipeline_parallel_size, 1)
        self.assertEqual(config.shm_size, "16Gi")
        self.assertEqual(config.model_path, "Qwen/Qwen2.5-0.5B-Instruct")
        self.assertEqual(config.model_name, "Qwen2.5-0.5B-Instruct")
        self.assertEqual(config.served_model_name, "Qwen2.5-0.5B-Instruct")
        self.assertEqual(config.dtype, "float16")
        self.assertEqual(config.model_cache_host_path, "/tmp/vllm-bench/model-cache")
        self.assertEqual(config.model_cache_mount_path, "/root/.cache/huggingface")
        self.assertEqual(config.persist_root, "/tmp/vllm-bench")
        self.assertEqual(config.bench_binary, "vllm-bench")
        self.assertEqual(config.bench_timeout_seconds, 600)
        self.assertEqual(config.bench_num_prompts, 2)
        self.assertEqual(config.target_env["HF_HUB_DISABLE_XET"], "1")
        self.assertEqual(
            config.pod_tolerations,
            [{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists", "effect": "NoSchedule"}],
        )

    def test_local_env_uses_registry_images_for_containerd_cluster(self):
        if not (ROOT / "configs" / "enving.env").is_file():
            self.skipTest("local configs/enving.env is not present")
        try:
            config = load_env_config(ROOT / "configs" / "enving.env")
        except ValueError as exc:
            self.skipTest(f"local configs/enving.env does not match current schema: {exc}")

        self.assertTrue(config.master_image)
        self.assertTrue(config.target_vllm_image)
        self.assertEqual(config.bench_binary, "vllm-bench")
        self.assertEqual(
            config.pod_tolerations,
            [{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists", "effect": "NoSchedule"}],
        )

    def test_smoke_hparams_are_small_enough_for_rtx4060(self):
        serve = json.loads((ROOT / "configs" / "serve_hparams.smoke.json").read_text())[0]
        bench = json.loads((ROOT / "configs" / "bench_hparams.smoke.json").read_text())[0]

        self.assertEqual(serve["--max-num-seqs"], 2)
        self.assertEqual(serve["--max-num-batched-tokens"], 4096)
        self.assertEqual(serve["--max-model-len"], 2048)
        self.assertEqual(bench["--random-input-len"], 32)
        self.assertEqual(bench["--random-output-len"], 16)
        self.assertEqual(bench["--request-rate"], 1)

    def test_env_renders_model_and_vendor_runtime_configs(self):
        env = load_env_config(ROOT / "configs" / "enving.example.env")
        payload = build_payload_from_files(
            env,
            ROOT / "configs" / "serve_hparams.smoke.json",
            ROOT / "configs" / "bench_hparams.smoke.json",
            run_id="run-001",
        )
        model = payload["model_config"]
        vendor = payload["vendor_profile"]

        self.assertEqual(model["model_path"], "Qwen/Qwen2.5-0.5B-Instruct")
        self.assertEqual(model["model_name"], "Qwen2.5-0.5B-Instruct")
        self.assertEqual(model["served_model_name"], "Qwen2.5-0.5B-Instruct")
        self.assertEqual(model["dtype"], "float16")
        self.assertEqual(vendor["target_vllm_image"], "vllm/vllm-openai:v0.8.5")
        self.assertEqual(vendor["resource_name"], "nvidia.com/gpu")
        self.assertEqual(vendor["resource_count"], 1)
        self.assertEqual(vendor["tensor_parallel_size"], 1)
        self.assertEqual(vendor["pipeline_parallel_size"], 1)
        self.assertEqual(vendor["shm_size"], "16Gi")

    def test_dockerfiles_include_required_smoke_runtime_tools(self):
        master = (ROOT / "docker" / "Dockerfile.master").read_text()

        self.assertIn("kubectl", master)
        self.assertIn("https://dl.k8s.io", master)
        self.assertIn("vllm-bench", master)
        self.assertIn("vllm_bench_platform.master.master", master)


if __name__ == "__main__":
    unittest.main()
