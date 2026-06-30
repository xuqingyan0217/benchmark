"""后端任务提交与查询模块。

backend 子包边界：
- 接收用户 run 配置，并在提交前完成 schema 校验。
- 构造 ConfigMap、PVC、Master Job 等 Kubernetes MVP 资源。
- 当前阶段不直接执行 benchmark，也不在本地生成压测结果。
- target vLLM Pod 和 bench-runner Pod 由后续 master-controller 编排。
- builder 模块只返回 manifest dict，便于测试和替换实际 Kubernetes client。
- submit 模块负责资源提交顺序，避免调用方散落 Kubernetes 创建逻辑。
- 查询接口后续应只读取 run 状态和结果索引，不改变运行中的 benchmark。
- 所有对外错误信息应保留中文字段名，便于平台用户定位配置问题。
- 这里的注释用于记录 OpenSpec 中确认过的职责分界，减少后续越界实现。
"""
