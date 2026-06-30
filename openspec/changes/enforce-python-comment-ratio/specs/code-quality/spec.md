## ADDED Requirements

### Requirement: Python 源码注释率不得低于 40%
系统 SHALL 对 `vllm_bench_platform/**/*.py` 统计整体注释率，并要求注释率不低于 40%。

#### Scenario: 注释率达标
- **WHEN** 运行注释率检查
- **THEN** `vllm_bench_platform/` 下 Python 源码整体注释率大于或等于 40%

#### Scenario: 注释率不达标
- **WHEN** 注释率低于 40%
- **THEN** 自动化测试失败并报告当前注释率

### Requirement: 注释必须服务维护
源码注释 MUST 解释业务意图、OpenSpec 约束、Kubernetes 资源设计、失败处理或维护注意点。

#### Scenario: 避免无意义注释
- **WHEN** 为源码添加注释
- **THEN** 注释说明原因和约束，而不是机械复述代码语法

### Requirement: 注释率检查使用固定口径
注释率检查 SHALL 使用固定统计口径：非空源码行作为分母，`#` 注释行和文档字符串行作为注释行。

#### Scenario: 统计口径一致
- **WHEN** 测试和开发者手动运行注释率检查
- **THEN** 两者得到相同统计结果
