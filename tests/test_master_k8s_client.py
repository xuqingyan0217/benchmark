import http.client
import unittest
from unittest.mock import patch


class MasterK8sClientTest(unittest.TestCase):
    def test_wait_http_ready_retries_remote_disconnect_until_healthy(self):
        from vllm_bench_platform.master.k8s_client import KubectlMasterClient

        calls = {"count": 0}

        def fake_urlopen(url, timeout):
            calls["count"] += 1
            if calls["count"] == 1:
                raise http.client.RemoteDisconnected("not ready yet")

            class Response:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

            return Response()

        with patch("vllm_bench_platform.master.k8s_client.urlopen", fake_urlopen), patch("time.sleep"):
            ready = KubectlMasterClient().wait_http_ready("http://target/health", timeout_seconds=5)

        self.assertTrue(ready)
        self.assertEqual(calls["count"], 2)

    def test_wait_pod_ready_returns_false_when_pod_phase_failed(self):
        from vllm_bench_platform.master.k8s_client import KubectlMasterClient

        calls = []

        def runner(args, input_text=None, timeout=None):
            calls.append(args)
            if args[:4] == ["kubectl", "get", "pod", "target"]:
                return '{"status":{"phase":"Failed"}}'
            return ""

        with patch("time.sleep"):
            ready = KubectlMasterClient(runner=runner).wait_pod_ready("target", "bench", timeout_seconds=5)

        self.assertFalse(ready)
        self.assertEqual(calls[0][:4], ["kubectl", "get", "pod", "target"])

    def test_wait_pod_ready_returns_false_for_oomkilled_container(self):
        from vllm_bench_platform.master.k8s_client import KubectlMasterClient

        calls = []

        def runner(args, input_text=None, timeout=None):
            calls.append(args)
            if args[:4] == ["kubectl", "get", "pod", "target"]:
                return (
                    '{"status":{"phase":"Running","containerStatuses":['
                    '{"state":{"terminated":{"reason":"OOMKilled"}}}'
                    ']}}'
                )
            return ""

        with patch("time.sleep"):
            client = KubectlMasterClient(runner=runner)
            ready = client.wait_pod_ready("target", "bench", timeout_seconds=5)
            reason = client.pod_failure_reason("target", "bench")

        self.assertFalse(ready)
        self.assertEqual(reason, "OOMKilled")
        self.assertEqual(calls[0][:4], ["kubectl", "get", "pod", "target"])


if __name__ == "__main__":
    unittest.main()
