"""master-controller 调用 bench-runner 的 HTTP client。

维护约束：
- base_url 默认指向 127.0.0.1:18080，因为两个容器位于同一个 Master Pod。
- 这里访问的是 bench-runner agent，不是 target vLLM Service。
- health timeout 失败时 controller 不应创建 target 资源，避免留下无人驱动的 Pod。
- `/run-bench` payload 保持 dict，便于 fake client 和 HTTP client 共用同一结构。
- `/shutdown` 在 best_config 写完后调用，避免 agent 过早退出丢失 raw 文件。
- HTTP request timeout 必须覆盖单次 benchmark 的真实耗时，首次 vLLM CLI 启动可能超过 30 秒。
- health timeout 和 request timeout 是两个不同维度，不能合并成一个配置。
- health timeout 只证明 agent HTTP 服务已经监听，不证明 vLLM CLI 已经完成冷启动。
- request timeout 覆盖的是 `/run-bench` 这个阻塞式 HTTP 调用的最长等待时间。
- bench-runner agent 会在请求线程内同步执行 `vllm bench serve`，所以 controller 必须等待。
- 如果 request timeout 小于 BENCH_TIMEOUT_SECONDS，raw_json/raw_log 可能已经写出但 summary 写不进来。
- 这类半成功状态会让 Job 失败且结果目录残缺，是 smoke 闭环最容易误判的场景。
- 默认 30 秒只适合单元测试和非常小的 warm run；真实 smoke 应由 Job env 显式覆盖。
- controller 不直接读取 BENCH_TIMEOUT_SECONDS，是为了避免和 bench-runner 容器环境耦合。
- 后端 Job builder 负责把同一份本地 env 同时注入 controller 和 bench-runner。
- 如果未来 bench-runner 改成异步任务 API，这里应保留 submit/poll 的超时边界。
- 如果未来支持多个 bench-runner endpoint，request timeout 应仍是每个 case 的 client 属性。
- urllib 的 timeout 不是总 wall-clock deadline；它约束 socket 操作，因此仍要让 agent 子进程自带 timeout。
- BrokenPipeError 往往说明 controller 已经先超时断开，而不是 bench-runner 本身失败。
- 遇到 BrokenPipeError 时应先检查 controller POST timeout，再检查 benchmark 命令退出码。
- 不在 `_post_json` 捕获 TimeoutError，是为了让 controller/job 明确暴露控制面失败。
- benchmark 语义失败仍由 bench-runner 返回 JSON，并由 controller 写入 failed_cases.jsonl。
- 这里的 JSON 解码保持严格；agent 返回非 JSON 代表协议破坏，应让 Job 失败。
- 所有路径都使用相对 path 参数拼接 base_url，避免调用方绕过 localhost 约束。
- HTTP 错误会抛给 controller，由 controller 决定是否记录 UNKNOWN/K8S 类失败。
- 不在这里实现重试；benchmark case 重试属于 master 执行循环语义。
- 标准库 urllib 足够满足 MVP，后续若引入 requests 也应保持接口不变。
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


class BenchRunnerClient:
    """针对 localhost:18080 的最小 HTTP client。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:18080",
        request_timeout_seconds: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = request_timeout_seconds

    def wait_health(self, timeout_seconds: int = 120) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                with urlopen(f"{self.base_url}/health", timeout=5) as response:
                    if response.status == 200:
                        return True
            except URLError:
                time.sleep(1)
        return False

    def run_bench(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._post_json("/run-bench", request)

    def shutdown(self) -> dict[str, Any]:
        return self._post_json("/shutdown", {})

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.request_timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
