"""bench-runner 本地压测 agent 模块。

bench-runner 子包边界：
- 运行在单个 benchmark case 对应的 Kubernetes Job 内。
- 输入来自 master-controller 生成的 case 配置和 target service 地址。
- 只负责调用 vllm-bench 或等价压测命令，不负责展开全局矩阵。
- 成功时写入 summary.csv 和 summary.jsonl 所需的原始指标。
- 失败时保留退出码、标准错误类型、原始日志路径和 target 身份。
- runner 不应该重启 target vLLM Pod，target 生命周期属于 master-controller。
- runner 不应该改写 run_meta.json 或 best_config.json 的全局选择逻辑。
- 这里目前只放包边界说明，后续实现时按 OpenSpec tasks 逐步填充。
- 注释保持中文，是为了让运维、算法和平台研发都能直接阅读排障语义。
"""
