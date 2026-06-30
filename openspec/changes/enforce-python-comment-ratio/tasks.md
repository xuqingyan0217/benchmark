## 1. OpenSpec 与项目约束

- [x] 1.1 将 `vllm_bench_platform/` Python 源码注释率不低于 40% 写入 AGENTS 和 OpenSpec config。

## 2. 自动化检查

- [x] 2.1 新增注释率统计工具，统计 `#` 注释行和文档字符串行。
- [x] 2.2 新增单元测试，要求 `vllm_bench_platform/` 整体注释率不低于 40%。

## 3. 源码注释

- [x] 3.1 为 `vllm_bench_platform/` 当前源码补充有意义中文注释和文档字符串。
- [x] 3.2 运行注释率检查，确认不低于 40%。

## 4. 验证

- [x] 4.1 运行 OpenSpec strict 校验。
- [x] 4.2 运行全量单元测试。
