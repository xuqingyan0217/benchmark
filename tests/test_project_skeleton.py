from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProjectSkeletonTest(unittest.TestCase):
    def test_approved_directories_and_package_markers_exist(self):
        expected_dirs = [
            ROOT / "vllm_bench_platform",
            ROOT / "vllm_bench_platform" / "backend",
            ROOT / "vllm_bench_platform" / "master",
            ROOT / "vllm_bench_platform" / "bench_runner",
            ROOT / "configs",
            ROOT / "manifests",
            ROOT / "docker",
        ]

        for path in expected_dirs:
            with self.subTest(path=path):
                self.assertTrue(path.is_dir())

        expected_package_markers = [
            ROOT / "vllm_bench_platform" / "__init__.py",
            ROOT / "vllm_bench_platform" / "backend" / "__init__.py",
            ROOT / "vllm_bench_platform" / "master" / "__init__.py",
            ROOT / "vllm_bench_platform" / "bench_runner" / "__init__.py",
        ]

        for path in expected_package_markers:
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

        expected_project_files = [
            ROOT / "pyproject.toml",
            ROOT / "tests" / "__init__.py",
        ]

        for path in expected_project_files:
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

    def test_runtime_modules_exist_after_mvp_apply_started(self):
        runtime_modules = [
            ROOT / "vllm_bench_platform" / "backend" / "cli.py",
            ROOT / "vllm_bench_platform" / "backend" / "api.py",
            ROOT / "vllm_bench_platform" / "master" / "master.py",
            ROOT / "vllm_bench_platform" / "bench_runner" / "bench_agent.py",
        ]

        implemented_modules = [path for path in runtime_modules if path.name != "api.py"]
        for path in implemented_modules:
            with self.subTest(path=path):
                self.assertTrue(path.exists())

        # HTTP 框架尚未引入，后端 API 仍由 CLI/函数入口代表 MVP 查询和提交能力。
        self.assertFalse((ROOT / "vllm_bench_platform" / "backend" / "api.py").exists())


if __name__ == "__main__":
    unittest.main()
