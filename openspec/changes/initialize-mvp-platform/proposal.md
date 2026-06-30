## 原因

我们需要先为“国产卡 vLLM 自动化压测平台”建立一套可追溯的 Spec-first MVP
契约，避免一开始就写成临时脚本。这个 change 的目标是在任何业务代码出现之前，
先明确平台为什么做、做什么、不做什么、怎么验收，以及后续实现任务如何拆分。

## 背景

平台面向运行在 Kubernetes 环境中的国产 AI 加速卡 vLLM 服务。它需要组合不同
vendor profile、vLLM 镜像、模型配置、服务端参数和压测参数，自动启动 target
vLLM 服务，执行压测，收集 raw output、日志、Kubernetes events，并产出 summary
和 best_config。

已确认的 MVP 运行形态：

```text
frontend -> backend -> Master Job -> Master Pod
                                  |-> master-controller
                                  |-> bench-runner on localhost:18080
```

阶段性方案比较结论：

- 不采用“600 个轻量 Worker Job”作为 MVP 主方案。该方案隔离性最强，但每个 case 都
  创建 Job/Pod，会带来大量 Kubernetes 调度、镜像启动、日志收集和资源清理开销；它可
  作为后续强隔离复测方案保留。
- 不采用“一个重 Job 内部单脚本跑完全部逻辑”作为 MVP 主方案。该方案效率较高，但
  控制逻辑、bench 逻辑、server 管理逻辑容易混在一起，后续失败恢复、日志定位、
  结果归档和平台化扩展都会变复杂。
- 当前方案采用后端创建 Master Job、Master Job 启动双容器 Master Pod、
  master-controller 动态创建 target vLLM Pod + Service、bench-runner 执行轻量
  vllm-bench、结果统一写入 PVC 的结构，作为 MVP 骨架封存。

## 目标

- 定义 MVP 阶段的 OpenSpec 契约，覆盖任务提交、Kubernetes 资源生成、Master Pod
  编排、target vLLM 生命周期、压测执行、结果写入、失败重试、资源清理和最优配置分析。
- 包含后端任务状态查询、结果下载、失败 case 查看能力的 MVP 接口契约。
- 明确只有 target vLLM Pod 申请国产卡资源。
- 在实现前形成可 review 的 proposal、design、specs、tasks。
- MVP 限定为单集群、单 namespace、单模型、单 vendor profile。

## 非目标

- 复杂前端页面。
- 多集群。
- 多 vendor 并发。
- 多模型并发。
- Prometheus 指标采集。
- Grafana 看板。
- Kueue 或 Volcano 集成。
- WebSocket 实时日志。
- 历史结果数据库。
- 高级 Pareto 分析。

## 用户故事

- 作为平台用户，我希望提交一次压测配置，后端即可创建完成自动化运行所需的
  Kubernetes 资源。
- 作为压测运维人员，我希望 bench_config 变化时复用同一个 target vLLM Pod，避免
  不必要的服务启动成本。
- 作为压测运维人员，我希望 serve_config 变化时重新创建 target Pod 和 Service，
  保证服务端参数变更相互隔离。
- 作为性能分析人员，我希望所有结果稳定写入 `/results/{run_id}/`，方便查看失败
  case、summary、raw output、server logs、events 和 best_config。

## 约束条件

- 用户确认本 change 的 proposal、design、specs、tasks 之前，不允许进入实现。
- 每一步实现都必须能回溯到本 OpenSpec change。
- `bench-runner` 和 `master-controller` 必须运行在同一个 Master Pod，并通过
  `localhost:18080` 通信。
- `bench-runner` 必须使用轻量 `vllm-project/vllm-bench`，不使用完整 Python vLLM
  环境。
- `bench-runner` 和 `master-controller` 不得申请国产卡资源。
- 只有 target vLLM Pod 申请配置中的国产卡资源。
- target vLLM Pod 不直接写压测结果。
- target vLLM Pod 的 logs 和 Kubernetes events 必须由 `master-controller` 抓取并
  写入 PVC。
- Master Pod 内两个容器共享 `/configs`、`/results` 和 `/work`。
- bench-runner 访问 target vLLM Pod 时不得使用 localhost，必须通过 target Service。
- MVP 阶段 PVC 可以使用 ReadWriteOnce；多 Worker Pod 并发写入时再评估 ReadWriteMany
  或对象存储。

## 变更内容

- 新增平台 MVP 的 OpenSpec capability 集合。
- 定义预期源码目录、配置文件、Kubernetes 资源、本地 bench-runner API、结果目录和
  编排行为。
- 补充 RBAC 权限、target Service 访问规则、失败类型、failed case 字段、PVC 访问模式、
  `/work` 共享目录和分阶段实现顺序。
- 定义后续实现任务清单。只有用户确认后才能按任务进入实现。
- 写入项目级 OpenSpec 和 Codex agent 上下文，保证后续工作遵守同一套范围和架构约束。

## 能力范围

### 新增能力

- `job-submission`：后端接受 MVP 压测任务请求，并创建启动运行所需的 ConfigMap、
  PVC 和 Master Job，并提供任务状态查询、结果下载、失败 case 查看接口契约。
- `master-orchestration`：master-controller 读取配置，控制 serve_config x
  bench_config 循环，管理 target 资源，执行失败重试、失败分类、资源清理并协调
  shutdown。
- `bench-runner-agent`：本地 bench-runner HTTP agent 提供 health、run-bench、
  shutdown 能力，并调用轻量 vllm-bench。
- `result-management`：平台将 raw output、summary、失败记录、target logs、target
  events、run metadata 和 best_config 写入规定的 PVC 目录。

### 修改能力

- 无。本 change 是项目初始 capability 集合。

## 验收标准

- 本 change 可以通过 OpenSpec strict 校验。
- change 包含 proposal、design、specs、tasks。
- specs 覆盖以上全部 MVP capabilities。
- tasks 已具备实现可执行性，但仍受用户确认门禁约束。
- proposal 阶段不创建任何业务代码。

## 风险点

- 不同国产卡厂商的 Kubernetes 资源字段存在差异；MVP 通过单一
  `vendor_profile.json` 收敛。
- vLLM health path 和 benchmark output 字段可能随镜像或版本变化；MVP 要求显式配置
  `health_path`，并保留 raw output。
- Kubernetes 清理失败可能留下 Pod 或 Service；设计必须包含清理和事件抓取行为。
- 国产卡 runtime 在 target Pod 删除后是否完全释放需要验证；MVP 需要在 serve_config
  切换时等待删除完成，并允许短暂 sleep。
- Master Job 长时间运行可能超过集群策略；MVP 需要设计 activeDeadlineSeconds 和异常
  兜底。
- best_config 分析容易扩展过度；MVP 使用简单确定性规则，不做高级 Pareto 分析。

## 影响范围

- 后续受影响的代码区域：
  - `vllm_bench_platform/backend/`
  - `vllm_bench_platform/master/`
  - `vllm_bench_platform/bench_runner/`
  - `configs/`
  - `manifests/`
  - `docker/`
- 后续受影响的接口：
  - 后端提交任务 API。
  - 本地 bench-runner `/health`、`/run-bench`、`/shutdown` API。
- 后续受影响的系统：
  - Kubernetes ConfigMap、PVC、Job、Pod、Service、RBAC、logs、events。
