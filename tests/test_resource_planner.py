import json
from pathlib import Path
import tempfile
import unittest


class ResourcePlannerTest(unittest.TestCase):
    def test_plan_model_resources_reads_metadata_and_calculates_tp_pp(self):
        from vllm_bench_platform.resource_planner import plan_model_resources

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "model.safetensors.index.json").write_text(
                json.dumps({"metadata": {"total_size": 40_000_000_000}}),
                encoding="utf-8",
            )
            (root / "config.json").write_text(
                json.dumps({"num_attention_heads": 40}),
                encoding="utf-8",
            )

            plan = plan_model_resources(root, memory_per_gpu_gb=24)

        self.assertEqual(plan.gpu_count, 4)
        self.assertEqual(plan.tensor_parallel_size, 4)
        self.assertEqual(plan.pipeline_parallel_size, 1)
        self.assertEqual(plan.tensor_parallel_size * plan.pipeline_parallel_size, plan.gpu_count)
        self.assertEqual(40 % plan.tensor_parallel_size, 0)

    def test_plan_model_resources_rounds_odd_multi_gpu_count_up(self):
        from vllm_bench_platform.resource_planner import plan_model_resources

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "model.safetensors.index.json").write_text(
                json.dumps({"metadata": {"total_size": 30_000_000_000}}),
                encoding="utf-8",
            )
            (root / "config.json").write_text(
                json.dumps({"num_attention_heads": 48}),
                encoding="utf-8",
            )

            plan = plan_model_resources(root, memory_per_gpu_gb=20)

        self.assertEqual(plan.gpu_count, 4)
        self.assertEqual(plan.tensor_parallel_size, 4)
        self.assertEqual(plan.pipeline_parallel_size, 1)

    def test_plan_model_resources_fails_without_required_metadata(self):
        from vllm_bench_platform.resource_planner import ResourcePlanningError, plan_model_resources

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text(json.dumps({"num_attention_heads": 16}), encoding="utf-8")

            with self.assertRaisesRegex(ResourcePlanningError, "model.safetensors.index.json"):
                plan_model_resources(root, memory_per_gpu_gb=24)


if __name__ == "__main__":
    unittest.main()
