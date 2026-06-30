## ADDED Requirements

### Requirement: 结果目录使用固定布局
平台 SHALL 将所有 run 输出写入 `/results/{run_id}/` 下的 MVP 结果目录结构。

#### Scenario: 初始化结果目录
- **WHEN** master-controller 启动一次 run
- **THEN** 它确保 `/results/{run_id}/` 下存在 `raw_json/`、`raw_logs/`、
  `server_logs/`、`events/` 目录

#### Scenario: 写入 run metadata
- **WHEN** master-controller 启动一次 run
- **THEN** 它写入 `run_meta.json`，包含 run ID、配置名称、namespace 和资源名称

#### Scenario: MVP 使用 ReadWriteOnce PVC
- **WHEN** 后端为 MVP run 创建结果 PVC
- **THEN** PVC 可以使用 ReadWriteOnce，因为写结果的容器位于同一个 Master Pod 内

### Requirement: master-controller 写入成功 summary
master-controller SHALL 为每个成功的 benchmark case 追加一条 summary 记录。

#### Scenario: 写入 CSV 和 JSONL summary
- **WHEN** 一个 benchmark case 成功
- **THEN** master-controller 向 `summary.csv` 和 `summary.jsonl` 追加等价的 case 信息

#### Scenario: 包含矩阵身份信息
- **WHEN** master-controller 写入 summary 记录
- **THEN** 记录包含 serve benchmark name、bench benchmark name、target endpoint
  identity、attempt count、raw output paths 和 parsed metrics

### Requirement: benchmark 失败重试一次
master-controller SHALL 在将 benchmark case 记录为失败前重试一次。

#### Scenario: 重试成功
- **WHEN** benchmark case 第一次尝试失败，第二次尝试成功
- **THEN** master-controller 写入成功 summary 记录，且 attempt count 为 `2`

#### Scenario: 重试仍失败
- **WHEN** benchmark case 两次尝试都失败
- **THEN** master-controller 将 case details、attempts 和 failure reason 追加到
  `failed_cases.jsonl`

#### Scenario: failed case 字段完整
- **WHEN** master-controller 写入 `failed_cases.jsonl`
- **THEN** 每条记录至少包含 `run_id`、`case_id`、`serve_config`、`bench_config`、
  `attempt`、`error_type`、`error_message`、`raw_log_path`、`target_pod_name`、
  `target_node_name`、`start_time`、`end_time`

#### Scenario: 使用标准失败类型
- **WHEN** master-controller 或 bench-runner 记录失败
- **THEN** `error_type` 使用 MVP 标准失败类型之一

### Requirement: MVP 失败类型受控
平台 SHALL 使用受控的 MVP 失败类型集合记录 benchmark、target 和 Kubernetes 失败。

#### Scenario: benchmark 失败类型
- **WHEN** benchmark timeout、命令失败、结果 JSON 缺失或解析失败
- **THEN** `error_type` 分别使用 `BENCH_TIMEOUT`、`BENCH_COMMAND_FAILED`、
  `RESULT_JSON_NOT_FOUND` 或 `RESULT_PARSE_FAILED`

#### Scenario: target 失败类型
- **WHEN** target Pod pending、启动失败、health timeout、server 崩溃、镜像拉取失败或删除 timeout
- **THEN** `error_type` 分别使用 `TARGET_POD_PENDING`、`TARGET_POD_FAILED`、
  `TARGET_HEALTH_TIMEOUT`、`TARGET_SERVER_CRASHED`、`TARGET_IMAGE_PULL_FAILED` 或
  `TARGET_POD_DELETE_TIMEOUT`

#### Scenario: Kubernetes 和未知失败类型
- **WHEN** Kubernetes API 调用失败或无法归类的错误发生
- **THEN** `error_type` 分别使用 `K8S_API_ERROR` 或 `UNKNOWN_ERROR`

#### Scenario: target Pod 强制删除记录
- **WHEN** target Pod 删除超时后被强制删除
- **THEN** master-controller 记录 `TARGET_POD_FORCE_DELETED`

### Requirement: 抓取 target logs 和 events
master-controller SHALL 在删除 target 资源前，为每组 serve_config 抓取 target vLLM Pod
logs 和 Kubernetes events。

#### Scenario: 抓取 server logs
- **WHEN** 某组 serve_config 完成，或在 target Pod 创建后失败
- **THEN** master-controller 将 target container logs 写入
  `/results/{run_id}/server_logs/`

#### Scenario: 抓取 target events
- **WHEN** 某组 serve_config 完成，或在 target Pod 创建后失败
- **THEN** master-controller 将相关 Kubernetes events 写入 `/results/{run_id}/events/`

### Requirement: 生成 best configuration
master-controller SHALL 在全部 benchmark case 完成后生成 `best_config.json`。

#### Scenario: 基于成功 case 生成 best config
- **WHEN** 至少一个 benchmark case 成功
- **THEN** master-controller 写入 `best_config.json`，包含被选中的 case、serve config
  identity、bench config identity 和用于选择的 metrics

#### Scenario: 无成功 case 时生成 best config
- **WHEN** 没有任何 benchmark case 成功
- **THEN** master-controller 写入 `best_config.json`，标记无可用成功 case，并保留失败
  summary 上下文
