"""bench-runner HTTP agent。

它只监听 Master Pod 内部的 localhost:18080，由 master-controller 调用；业务逻辑委托给
vllm_bench_runner，handler 只负责 JSON 编解码和 shutdown 编排。

维护约束：
- agent 不暴露 Kubernetes Service，只在 Master Pod 网络命名空间内给 controller 使用。
- 标准库 HTTP server 足够支撑 MVP，避免为了三个 endpoint 引入 Web 框架和镜像依赖。
- `/health` 只表达 agent readiness，不代表 target vLLM 已经 ready。
- `/run-bench` 的请求体必须包含 target endpoint 和矩阵身份，便于 raw 文件命名和 summary。
- `/shutdown` 返回后异步停止 server，避免在响应尚未写回时关闭 socket。
- handler 不直接拼命令、不解析结果，业务逻辑集中在 vllm_bench_runner 便于单测。
- 环境变量只控制 agent 自身执行参数，不应该影响 master-controller 的 target 生命周期。
- `RESULTS_ROOT` 默认 `/results`，对应 Master Job 的 PVC 挂载。
- `WORK_DIR` 默认 `/work`，对应 Master Pod 内两个容器共享的临时目录。
- agent 日志目前交给容器 stdout；最终 benchmark artifact 由 raw log/raw json 承担。
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
from typing import Any

from vllm_bench_platform.bench_runner.vllm_bench_runner import request_from_payload, run_bench_case


class BenchAgentHandler(BaseHTTPRequestHandler):
    """标准库 HTTP handler，避免 MVP 阶段引入 Web 框架依赖。"""

    server: "BenchAgentServer"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._write_json({"healthy": True})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/run-bench":
            payload = self._read_json()
            result = run_bench_case(
                request_from_payload(payload),
                results_root=self.server.results_root,
                work_dir=self.server.work_dir,
                bench_command=self.server.bench_command,
                timeout_seconds=self.server.timeout_seconds,
                num_prompts=self.server.num_prompts,
                runner=self.server.process_runner,
            )
            self._write_json(result)
            return
        if self.path == "/shutdown":
            self._write_json({"accepted": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self.send_error(404)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _write_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class BenchAgentServer(ThreadingHTTPServer):
    """携带运行目录和 bench 命令配置的 HTTP server。"""

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        results_root: str | Path,
        work_dir: str | Path,
        bench_command: str,
        timeout_seconds: int,
        num_prompts: int,
        process_runner=subprocess.run,
    ):
        super().__init__(server_address, BenchAgentHandler)
        self.results_root = Path(results_root)
        self.work_dir = Path(work_dir)
        self.bench_command = bench_command
        self.timeout_seconds = timeout_seconds
        self.num_prompts = num_prompts
        self.process_runner = process_runner


def main() -> None:
    server = BenchAgentServer(
        ("127.0.0.1", 18080),
        results_root=os.environ.get("RESULTS_ROOT", "/results"),
        work_dir=os.environ.get("WORK_DIR", "/work"),
        bench_command=os.environ.get("BENCH_COMMAND", "vllm bench serve"),
        timeout_seconds=int(os.environ.get("BENCH_TIMEOUT_SECONDS", "1800")),
        num_prompts=int(os.environ.get("BENCH_NUM_PROMPTS", "10")),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
