## ADDED Requirements

### Requirement: master-controller 加载压测矩阵
master-controller SHALL 读取挂载的配置文件，并构造 serve_config x bench_config 执行矩阵。

#### Scenario: 加载合法矩阵
- **WHEN** Master Pod 启动且四个必需配置文件都存在
- **THEN** master-controller 在创建 target 资源前加载 serve configs、bench configs、
  vendor profile 和 model config

#### Scenario: 拒绝缺少名称的矩阵项
- **WHEN** 任意 serve_config 或 bench_config 缺少 `_benchmark_name`
- **THEN** master-controller 在创建 target 资源前使 run 失败

### Requirement: master-controller 等待 bench-runner ready
master-controller SHALL 在启动 target 生命周期前，等待 `localhost:18080` 上的
bench-runner `/health` ready。

#### Scenario: bench-runner 变为 healthy
- **WHEN** bench-runner `/health` 在 timeout 前返回 healthy
- **THEN** master-controller 继续创建第一组 target Pod 和 Service

#### Scenario: bench-runner health timeout
- **WHEN** bench-runner `/health` 在 timeout 前未变为 healthy
- **THEN** master-controller 不创建 target vLLM 资源并使 run 失败

### Requirement: serve_config 变化时重建 target 资源
针对每个 serve_config，master-controller SHALL 创建新的 target vLLM Pod 和 target
Service，并在进入下一组 serve_config 前删除它们。

#### Scenario: 第一组 serve_config 创建 target 资源
- **WHEN** master-controller 开始处理一组 serve_config
- **THEN** 它为该 serve_config 创建一个 target vLLM Pod 和一个 target Service

#### Scenario: 下一组 serve_config 等待清理完成
- **WHEN** master-controller 完成一组 serve_config 下的所有 bench configs
- **THEN** 它抓取 logs/events，删除 target Service 和 target Pod，等待清理完成，然后才开始下一组 serve_config

#### Scenario: target 删除后释放缓冲
- **WHEN** target Pod 删除完成
- **THEN** master-controller 可以等待 5 到 10 秒，以降低国产卡 runtime 释放不完整带来的影响

### Requirement: bench_config 变化时复用 target 资源
在同一组 serve_config 内，master-controller SHALL 使用同一个 target vLLM Pod 和
Service 执行全部 bench_config case。

#### Scenario: 多个 bench_config 共用一个 target
- **WHEN** 一组 serve_config 下存在多个 bench_config case
- **THEN** master-controller 针对每个 bench_config 调用一次 bench-runner，且不在
  bench_config 之间重建 target Pod 或 Service

### Requirement: target Pod 申请国产卡资源
target vLLM Pod SHALL 申请 `vendor_profile.json` 中配置的 accelerator 资源。

#### Scenario: 构建 target Pod 资源
- **WHEN** master-controller 构建 target Pod spec
- **THEN** target container 申请 `vendor_profile.resource_name` 和
  `vendor_profile.resource_count`

#### Scenario: 应用 vendor 调度字段
- **WHEN** master-controller 构建 target Pod spec
- **THEN** 它应用配置中的 vendor env、node selector、tolerations、runtime class、
  shared-memory size 和 extra serve args

### Requirement: Target Service 提供稳定访问入口
master-controller SHALL 为每个 target vLLM Pod 创建对应 target Service，供 bench-runner
访问 target。

#### Scenario: target Service 名称包含 run 和 serve 身份
- **WHEN** master-controller 为 serve_config 创建 target Service
- **THEN** Service 名称包含 `run_id` 和 serve benchmark name

#### Scenario: bench-runner 不使用 localhost 访问 target
- **WHEN** master-controller 构造传给 bench-runner 的 target endpoint
- **THEN** endpoint 使用 target Service 名称和 `vendor_profile.port`，而不是 localhost 或 Pod IP

### Requirement: master-controller 等待 target ready
master-controller SHALL 在执行某组 serve_config 的 benchmark case 前，等待 target
vLLM health endpoint ready。

#### Scenario: target 变为 ready
- **WHEN** target Service 在 `vendor_profile.health_path` 上成功响应
- **THEN** master-controller 开始执行该 serve_config 下的 bench configs

#### Scenario: target readiness 失败
- **WHEN** target health endpoint 在 timeout 前未变为 ready
- **THEN** master-controller 记录失败上下文，抓取可用 events，并清理 target 资源

#### Scenario: target 启动失败跳过当前 serve
- **WHEN** target vLLM Pod 启动失败或进入失败状态
- **THEN** master-controller 记录失败类型，跳过当前 serve_config 下所有 bench_config，并清理 target 资源

#### Scenario: target 中途崩溃
- **WHEN** target vLLM Pod 在 bench_config 循环中崩溃
- **THEN** master-controller 记录失败，删除 target Service 和 target Pod，并进入下一组 serve_config

### Requirement: Master Pod 使用最小 RBAC 权限
Master Pod SHALL 通过 ServiceAccount、Role、RoleBinding 获得管理 target 生命周期和抓取日志事件所需的最小 Kubernetes API 权限。

#### Scenario: RBAC 包含 target 生命周期权限
- **WHEN** 生成 RBAC manifests
- **THEN** Role 包含 pods 和 services 的 get/list/watch/create/delete/patch 权限

#### Scenario: RBAC 包含日志事件读取权限
- **WHEN** 生成 RBAC manifests
- **THEN** Role 包含 pods/log 的 get/list 权限和 events 的 get/list/watch 权限

#### Scenario: RBAC 包含只读配置和 Job 状态权限
- **WHEN** 生成 RBAC manifests
- **THEN** Role 包含 configmaps 的 get/list 权限和 jobs 的 get/list/watch 权限

### Requirement: master-controller 关闭 bench-runner
全部 serve_config 和 bench_config case 完成后，master-controller SHALL 请求
bench-runner shutdown。

#### Scenario: 分析后 shutdown
- **WHEN** best-config 分析结果已经写入
- **THEN** master-controller 调用 `localhost:18080` 上的 `POST /shutdown`
