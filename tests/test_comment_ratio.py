import unittest


class CommentRatioTest(unittest.TestCase):
    def test_vllm_bench_platform_comment_ratio_is_at_least_40_percent(self):
        from tools.comment_ratio import calculate_comment_ratio

        result = calculate_comment_ratio("vllm_bench_platform")

        self.assertGreaterEqual(
            result.ratio,
            0.40,
            f"当前注释率为 {result.ratio:.2%}，低于 40%",
        )


if __name__ == "__main__":
    unittest.main()
