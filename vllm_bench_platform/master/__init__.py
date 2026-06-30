"""Master Pod 控制器模块。

master-controller 子包边界：
- 运行在 Kubernetes Master Job 内部，是一次 run 的集群内编排者。
- 读取 `/configs` 中由 backend ConfigMap 挂载的四个配置文件。
- 按 serve_config x bench_config 展开 benchmark case 矩阵。
- serve_config 变化时创建或重建 target vLLM server Pod。
- bench_config 变化时创建 bench-runner Job，并复用当前 target server。
- 负责等待 target health check 通过，再启动对应 benchmark case。
- 负责收集 target server 日志、事件和失败上下文，交给结果管理模块落盘。
- 当前 MVP 不在这里实现多集群调度，也不做跨 run 的资源复用。
- 后续具体实现必须保持“master 编排、runner 执行、backend 提交”的边界。
"""
