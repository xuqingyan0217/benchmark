## 上下文

本 change 初始化“国产卡 vLLM 自动化压测平台”的 MVP 技术设计。当前仓库只有
OpenSpec 和 Codex 工作流元数据，因此本设计先定义实现契约，不创建业务代码。

相关角色：

- 平台用户：提交压测任务。
- 压测运维人员：查看运行状态、日志、失败 case 和 summary。
- 性能分析人员：比较压测结果并识别最优配置。
- 平台维护者：在 MVP 之后扩展多 vendor、多模型、多集群等能力。

主要约束：

- 用户确认 proposal、design、specs、tasks 之前，不进入实现。
- MVP 只支持单集群、单 namespace、单模型、单 vendor profile。
- 只有 target vLLM Pod 申请国产卡资源。
- bench-runner 和 master-controller 不申请国产卡资源。
- target vLLM Pod 不直接写压测结果。
- bench-runner 和 target vLLM Pod 不在同一个 Pod 内，不能通过 localhost 访问 target；
  必须通过 target Service 访问。
- Master Pod 内两个容器共享 `/configs`、`/results` 和 `/work`。

## 目标 / 非目标

**目标：**

- 提供 MVP 后端 API，用于创建 ConfigMap、PVC 和 Master Job。
- 提供后端任务状态查询、结果下载和失败 case 查看接口。
- 运行包含 `master-controller` 和 `bench-runner` 的双容器 Master Pod。
- 执行确定性的 serve_config x bench_config 双层循环。
- bench_config 变化时复用 target vLLM Pod。
- serve_config 变化时重新创建 target Pod 和 Service。
- 将 raw output、summary、失败记录、target logs、target events、run metadata 和
  best_config 写入 `/results/{run_id}/`。
- 尽量让模块可以在无真实 Kubernetes 集群的情况下做单元测试。

**非目标：**

- 复杂前端、历史数据库、WebSocket 日志、Prometheus、Grafana、Kueue、Volcano、
  多集群、多 vendor 并发、多模型并发、高级 Pareto 分析。

## 架构

```text
backend
  | submit job request
  v
Kubernetes ConfigMap + PVC + Master Job
  |
  v
Master Pod
  |-- master-controller
  |     | 读取挂载配置
  |     | 创建/删除 target Pod + Service
  |     | 等待 bench-runner 和 target health
  |     | 调用 localhost:18080/run-bench
  |     | 写 summary、logs、events、analysis
  |
  |-- bench-runner
        | localhost:18080 HTTP agent
        | 调用 vllm-project/vllm-bench
        | 写 raw_json 和 raw_logs
```

后端准备一次 run 时负责校验输入配置并创建：

- ConfigMap：包含 `serve_hparams.json`、`bench_hparams.json`、
  `vendor_profile.json`、`model_config.json`。
- PVC：由 Master Pod 挂载到 `/results`。
- Master Job：Pod 中包含两个容器，分别是 `master-controller` 和 `bench-runner`。

Master Job 是运行边界。后端负责启动它，但 serve/bench matrix 的执行由 Master Pod
内部的 `master-controller` 控制。

MVP 明确排除两个备选架构：

- 600 个轻量 Worker Job：隔离性强，但每个 case 都创建 Job/Pod，调度、镜像启动、
  日志收集和资源清理开销过高，不作为 MVP 主路径。
- 单容器重 Job 大脚本：效率较高，但控制逻辑、bench 逻辑和 server 管理逻辑容易混杂，
  不利于失败恢复、日志定位和平台化扩展。

## 数据流

1. 用户向后端提交 MVP run 请求。
2. 后端校验必需配置并生成 `run_id`。
3. 后端创建 ConfigMap、PVC、Master Job。
4. Master Job 启动 Master Pod。
5. `bench-runner` 在 `localhost:18080` 启动 HTTP agent。
6. `master-controller` 读取挂载配置，并等待 bench-runner `/health`。
7. 针对每个 serve_config：
   - 根据 `vendor_profile.json` 和 `model_config.json` 创建 target vLLM Pod。
   - 创建 target Service。
   - 等待配置的 target `health_path`。
   - 针对每个 bench_config：
     - 调用 bench-runner `/run-bench`。
     - bench-runner 针对 target Service 调用 `vllm-bench`。
     - bench-runner 写入 raw JSON 和 raw logs。
     - master-controller 追加 summary 记录。
     - 失败重试一次；重试后仍失败则记录 failed case。
   - 抓取 target server logs 和 Kubernetes events。
   - 删除 target Service 和 target Pod。
   - 等待清理完成。
   - 可在 target Pod 删除完成后 sleep 5 到 10 秒，给国产卡 runtime 释放资源留出缓冲。
8. 生成 `best_config.json`。
9. 调用 bench-runner `/shutdown`。
10. 全部工作完成后 Master Job 正常退出。

## 组件职责

后端：

- 负责 submit-job API，以及 ConfigMap、PVC、Master Job 的 Kubernetes 资源创建。
- 负责任务状态查询、结果下载、失败 case 查看接口。
- 不执行压测循环。
- 不解析压测结果。

master-controller：

- 负责 matrix 加载、target Pod/Service 生命周期、health 等待、bench-runner client
  调用、失败重试、summary 写入、failed case 写入、server logs/events 抓取、
  best_config 生成和最终 shutdown。
- 在 Master Pod 内通过 Kubernetes API 操作资源。
- 需要通过 ServiceAccount、Role、RoleBinding 获得最小 RBAC 权限。
- 自身不申请国产卡资源。

bench-runner：

- 负责本地 HTTP agent 和 `vllm-bench` 进程调用。
- 将 raw benchmark JSON 和 logs 保存到 run 结果目录。
- 向 master-controller 返回结构化结果元数据。
- 自身不申请国产卡资源。

target vLLM Pod：

- 运行配置中的国产卡 vLLM 镜像。
- 申请 `vendor_profile.resource_name` 和 `vendor_profile.resource_count`。
- 接收模型配置和 serve 参数。
- 通过 target Service 暴露 OpenAI-compatible API。

Target Service：

- 每个 target vLLM Pod 对应一个 target Service。
- Service 名称包含 run_id 和 serve benchmark name，形成稳定访问入口，例如
  `vllm-target-{run_id}-{serve_name}`。
- bench-runner 通过 `http://vllm-target-{run_id}-{serve_name}:{port}` 访问 target，
  不依赖 Pod IP。

## 配置结构

`serve_hparams.json`：

- JSON array，每项是一个 object。
- 每项必须包含 `_benchmark_name`。
- 其他 key 是 vLLM serve CLI 参数，例如 `--max-num-seqs`、
  `--max-num-batched-tokens`。

`bench_hparams.json`：

- JSON array，每项是一个 object。
- 每项必须包含 `_benchmark_name`。
- 其他 key 是 vllm-bench CLI 参数，例如 `--random-input-len`、
  `--random-output-len`、`--request-rate`。

`vendor_profile.json`：

- `vendor_name`
- `target_vllm_image`
- `resource_name`
- `resource_count`
- `env`
- `node_selector`
- `tolerations`
- `runtime_class_name`
- `shm_size`
- `port`
- `health_path`
- `extra_serve_args`

`model_config.json`：

- `model_name`
- `model_path`
- `served_model_name`
- `trust_remote_code`
- `dtype`
- 可选 `tokenizer_path`

## 接口设计

后端 MVP API：

- `POST /api/runs`
  - 接收 run metadata 和四个必需配置 payload 或引用。
  - 校验 MVP 约束。
  - 创建 ConfigMap、PVC、Master Job。
  - 返回 `run_id`、namespace、Master Job 名称。
- `GET /api/runs/{run_id}`
  - 返回任务状态、Master Job 名称、开始/结束时间、成功/失败统计和结果目录位置。
- `GET /api/runs/{run_id}/results`
  - 返回结果文件列表或下载入口。
- `GET /api/runs/{run_id}/failed-cases`
  - 返回 `failed_cases.jsonl` 中的失败 case 摘要。

bench-runner 本地 API：

- `GET /health`
  - 返回 agent readiness。
- `POST /run-bench`
  - 接收 target endpoint、run_id、serve benchmark name、bench benchmark name、
    benchmark 参数和 output paths。
  - 执行 `vllm-bench`。
  - 写入 raw JSON 和 raw logs。
  - 返回成功/失败状态和解析出的 metrics。
- `POST /shutdown`
  - master-controller 完成全部 case 后请求 bench-runner 优雅退出。

## Kubernetes 资源设计

后端创建的资源：

- ConfigMap：保存四个配置文件。
- PVC：Master Pod 挂载到 `/results`，MVP 可使用 ReadWriteOnce。
- Master Job：拥有双容器 Master Pod。
- ServiceAccount、Role、RoleBinding：为 Master Pod 提供最小 Kubernetes API 权限。

Master Pod 共享挂载：

- `/configs`：读取四个配置文件。
- `/results`：保存 run 结果。
- `/work`：容器间临时协作目录。

Master Pod 最小 RBAC 权限：

- `pods`: `get`、`list`、`watch`、`create`、`delete`、`patch`
- `pods/log`: `get`、`list`
- `services`: `get`、`list`、`watch`、`create`、`delete`、`patch`
- `events`: `get`、`list`、`watch`
- `configmaps`: `get`、`list`
- `jobs`: `get`、`list`、`watch`

master-controller 创建的资源：

- Target Pod：
  - 使用 `vendor_profile.target_vllm_image`。
  - 只有 target container 申请配置的国产卡资源。
  - 应用 vendor env、node selector、tolerations、runtime class、shm size 和
    serve args。
- Target Service：
  - 在 `vendor_profile.port` 暴露 target Pod。
  - bench-runner 使用该 Service 作为压测 target endpoint。

## 结果目录

所有输出写入：

```text
/results/{run_id}/
  summary.csv
  summary.jsonl
  failed_cases.jsonl
  best_config.json
  run_meta.json
  raw_json/
  raw_logs/
  server_logs/
  events/
```

`summary.csv` 和 `summary.jsonl` 每个成功 benchmark case 一条记录。
`failed_cases.jsonl` 保存重试后仍失败的 case。`best_config.json` 使用实现阶段确认的
简单确定性规则记录 MVP 最优配置。`run_meta.json` 记录 run ID、输入元数据、时间戳和
资源名称。

`failed_cases.jsonl` 每条记录至少包含：

- `run_id`
- `case_id`
- `serve_config`
- `bench_config`
- `attempt`
- `error_type`
- `error_message`
- `raw_log_path`
- `target_pod_name`
- `target_node_name`
- `start_time`
- `end_time`

## 重试、失败和清理行为

- 每个 benchmark case 先执行一次。
- 第一次失败时，master-controller 重试一次。
- 重试仍失败时，master-controller 写入 `failed_cases.jsonl`，然后继续下一个
  bench_config。
- 每组 serve_config 清理前抓取 target server logs 和 events。
- 每组 serve_config 都必须删除 target Service 和 target Pod，并等待删除完成，再进入
  下一组 serve_config。
- target health 或 benchmark 执行失败时，controller 仍应尝试清理已创建资源。
- target vLLM Pod 启动失败时，跳过当前 serve_config 下所有 bench_config。
- target vLLM Pod 中途崩溃时，记录失败，删除 target 资源，进入下一组 serve_config。
- target Pod 删除超时时，允许强制删除并记录 `TARGET_POD_FORCE_DELETED`。

MVP 失败类型：

- `BENCH_TIMEOUT`
- `BENCH_COMMAND_FAILED`
- `RESULT_JSON_NOT_FOUND`
- `RESULT_PARSE_FAILED`
- `TARGET_POD_PENDING`
- `TARGET_POD_FAILED`
- `TARGET_HEALTH_TIMEOUT`
- `TARGET_SERVER_CRASHED`
- `TARGET_IMAGE_PULL_FAILED`
- `TARGET_POD_DELETE_TIMEOUT`
- `TARGET_POD_FORCE_DELETED`
- `K8S_API_ERROR`
- `UNKNOWN_ERROR`

## 技术决策

### 决策：使用双容器 Master Pod

理由：`master-controller` 和 `bench-runner` 放在同一个 Pod 内，可以通过
`localhost:18080` 快速通信，避免把 bench agent 暴露成集群 Service。

备选方案：将 bench-runner 做成独立 Job 或 Service。MVP 不采用，因为它会增加调度、
服务发现、生命周期和清理复杂度。

### 决策：target 生命周期由 master-controller 管理

理由：后端只负责提交工作，Master Job 负责执行工作。这样每次 run 都是自包含的，
serve_config 级别清理也靠近实际循环逻辑。

备选方案：由后端直接管理每个 target Pod。MVP 不采用，因为后端重启或失败会让运行归属
和清理逻辑更复杂。

### 决策：使用 JSON 文件作为 MVP 配置契约

理由：四个配置文件直接对应当前需求模型，也可以通过 ConfigMap 挂载到 Master Pod。

备选方案：把配置存入数据库。数据库不属于 MVP。

### 决策：只有 target vLLM Pod 申请国产卡资源

理由：控制容器不需要国产卡。只让 target Pod 申请资源，可以避免浪费稀缺硬件，并让调度
意图清晰。

备选方案：为了方便让 Master Pod 也申请国产卡资源。该方案违反核心规则，明确不采用。

### 决策：bench-runner 通过 target Service 访问 target vLLM

理由：bench-runner 和 target vLLM Pod 不在同一个 Pod 内，不能通过 localhost 通信。
Service 提供稳定访问入口，避免直接依赖 Pod IP。

备选方案：直接访问 target Pod IP。MVP 不采用，因为 Pod IP 不稳定，也会让健康检查和
日志定位更脆弱。

### 决策：MVP 使用 PVC ReadWriteOnce

理由：MVP 中写结果的两个容器位于同一个 Master Pod，共享同一个卷，不涉及多个 Pod
并发写同一个 PVC。

备选方案：使用 ReadWriteMany 或对象存储。当前不采用，后续多 Worker Pod 并发写结果时
再评估。

## 风险 / 权衡

- 不同 vendor 的 Kubernetes 字段存在差异 -> MVP 将 vendor 差异集中到
  `vendor_profile.json`。
- target health 行为可能随 vLLM 镜像不同而变化 -> MVP 使用可配置 `health_path`。
- benchmark JSON 可能随 `vllm-bench` 版本变化 -> parser 必须保留 raw JSON，并允许
  可选 metrics 缺失。
- Kubernetes API 错误可能导致清理失败 -> controller 必须记录 events，并在失败路径
  也尝试清理。
- 同一 serve_config 下多个 bench_config 仍可能存在轻微状态影响 -> MVP 保留该效率权衡，
  后续可用强隔离复测方案验证关键结果。
- target Pod 删除后国产卡 runtime 是否完全释放需要实测 -> MVP 等待删除完成，并保留
  5 到 10 秒释放缓冲。
- Master Job 长时间运行可能被集群策略中断 -> Master Job 需要配置 activeDeadlineSeconds
  和失败状态记录。
- PVC 文件数量和日志体积可能膨胀 -> MVP 按目录归档 raw logs/raw JSON，并在 summary 中
  只保存路径和关键指标。
- best_config 分析可能过度简化 -> MVP 使用简单确定性规则，推迟高级 Pareto 分析。

## 推荐实现顺序

第一阶段：本地验证 bench-runner。

- 构建 bench-runner 镜像或本地运行环境。
- 启动 `bench_agent.py`。
- 调用 `/health`。
- 调用 `/run-bench`。
- 确认生成 raw_json 和 raw_logs。

第二阶段：实现 master-controller 最小闭环。

- 读取配置。
- 创建一个 target vLLM Pod。
- 创建一个 target Service。
- 等待 target health。
- 调用 bench-runner 跑一组 bench。
- 写入 summary.csv。
- 删除 target Service 和 target Pod。

第三阶段：扩展到小矩阵。

- 使用 3 组 serve_config 和 4 组 bench_config。
- 验证失败重试、日志保存和 Pod 删除。

第四阶段：扩展到完整矩阵。

- 支持约 20 组 serve_config 和 30 组 bench_config。
- 产出约 600 行 summary。
- 生成 failed_cases.jsonl 和 best_config.json。

第五阶段：补充后端接口。

- 提交任务。
- 查询任务状态。
- 下载结果。
- 查看失败 case。
- 删除历史任务。

## 迁移计划

这是项目第一个 change，没有生产迁移。用户确认后，后续实现应按 tasks 逐项推进，并将
生成代码放在推荐源码目录下。

实现阶段回滚策略：

- 对未完成任务产生的代码变更进行回退。
- OpenSpec artifact 继续作为修订设计和任务的事实来源。

## 待确认问题

- 后端和 bench-runner HTTP API 使用哪个 Python Web framework？MVP 实施计划可以默认
  FastAPI，除非用户指定其他方案。
- `best_config.json` 在多个吞吐/延迟指标同时存在时使用哪个简单指标？MVP 实施计划可以
  默认选择最高 throughput，同时保留 latency 字段供人工查看。
