import unittest


class ResourcePlannerTest(unittest.TestCase):
    def test_plan_model_resources_reads_hugging_face_metadata_and_calculates_tp_pp(self):
        from vllm_bench_platform.resource_planner import plan_model_resources

        def fetch_json(url, token):
            if url.endswith("/resolve/main/config.json"):
                return {"num_attention_heads": 40}
            if "/api/models/" in url:
                return {"siblings": [{"rfilename": "model.safetensors", "size": 40_000_000_000}]}
            raise AssertionError(url)

        plan = plan_model_resources(
            memory_per_gpu_gb=24,
            model_id="org/model",
            fetch_json=fetch_json,
        )

        self.assertEqual(plan.gpu_count, 4)
        self.assertEqual(plan.tensor_parallel_size, 4)
        self.assertEqual(plan.pipeline_parallel_size, 1)
        self.assertEqual(plan.tensor_parallel_size * plan.pipeline_parallel_size, plan.gpu_count)
        self.assertEqual(40 % plan.tensor_parallel_size, 0)

    def test_plan_model_resources_prefers_hugging_face_model_info(self):
        from vllm_bench_platform.resource_planner import plan_model_resources

        def fetch_json(url, token):
            if url.endswith("/resolve/main/config.json"):
                return {"num_attention_heads": 32}
            if "/api/models/" in url:
                return {
                    "siblings": [
                        {"rfilename": "model-00001-of-00002.safetensors", "size": 20_000_000_000},
                        {"rfilename": "model-00002-of-00002.safetensors", "size": 20_000_000_000},
                        {"rfilename": "tokenizer.json", "size": 10_000},
                    ]
                }
            raise AssertionError(url)

        plan = plan_model_resources(
            memory_per_gpu_gb=24,
            model_id="org/small-model",
            fetch_json=fetch_json,
        )

        self.assertEqual(plan.gpu_count, 4)
        self.assertEqual(plan.tensor_parallel_size, 4)
        self.assertEqual(plan.pipeline_parallel_size, 1)

    def test_plan_model_resources_uses_hugging_face_index_when_sibling_sizes_are_missing(self):
        from vllm_bench_platform.resource_planner import plan_model_resources

        def fetch_json(url, token):
            if url.endswith("/resolve/main/config.json"):
                return {"num_attention_heads": 48}
            if "/api/models/" in url:
                return {"siblings": [{"rfilename": "model.safetensors"}]}
            if url.endswith("/resolve/main/model.safetensors.index.json"):
                return {"metadata": {"total_size": 30_000_000_000}}
            raise AssertionError(url)

        plan = plan_model_resources(
            memory_per_gpu_gb=20,
            model_id="org/index-model",
            fetch_json=fetch_json,
        )

        self.assertEqual(plan.gpu_count, 4)
        self.assertEqual(plan.tensor_parallel_size, 4)

    def test_plan_model_resources_uses_fallback_model_id_for_local_model_path(self):
        from vllm_bench_platform.resource_planner import plan_model_resources

        seen_urls = []

        def fetch_json(url, token):
            seen_urls.append(url)
            if url.endswith("/resolve/main/config.json"):
                return {"num_attention_heads": 16}
            if "/api/models/" in url:
                return {"siblings": [{"rfilename": "model.safetensors", "size": 1_000_000_000}]}
            raise AssertionError(url)

        plan = plan_model_resources(
            memory_per_gpu_gb=8,
            model_id="/models/local-qwen",
            fallback_model_id="Qwen/Qwen2.5-0.5B-Instruct",
            fetch_json=fetch_json,
        )

        self.assertEqual(plan.gpu_count, 1)
        self.assertTrue(any("Qwen/Qwen2.5-0.5B-Instruct" in url for url in seen_urls))

    def test_plan_model_resources_rounds_odd_multi_gpu_count_up(self):
        from vllm_bench_platform.resource_planner import plan_model_resources

        def fetch_json(url, token):
            if url.endswith("/resolve/main/config.json"):
                return {"num_attention_heads": 48}
            if "/api/models/" in url:
                return {"siblings": [{"rfilename": "model.safetensors", "size": 30_000_000_000}]}
            raise AssertionError(url)

        plan = plan_model_resources(
            memory_per_gpu_gb=20,
            model_id="org/model",
            fetch_json=fetch_json,
        )

        self.assertEqual(plan.gpu_count, 4)
        self.assertEqual(plan.tensor_parallel_size, 4)
        self.assertEqual(plan.pipeline_parallel_size, 1)

    def test_plan_model_resources_fails_without_hugging_face_repo_id(self):
        from vllm_bench_platform.resource_planner import ResourcePlanningError, plan_model_resources

        with self.assertRaisesRegex(ResourcePlanningError, "Hugging Face repo id"):
            plan_model_resources(memory_per_gpu_gb=24, model_id="/models/local-only")


if __name__ == "__main__":
    unittest.main()
