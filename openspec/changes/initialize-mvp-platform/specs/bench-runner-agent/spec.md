## ADDED Requirements

### Requirement: bench-runner 暴露本地 health endpoint
bench-runner SHALL 在 `localhost:18080` 暴露本地 HTTP health endpoint。

#### Scenario: agent 返回 healthy
- **WHEN** bench-runner 已初始化 HTTP agent
- **THEN** `GET /health` 向 master-controller 返回 healthy 响应

### Requirement: bench-runner 执行压测请求
bench-runner SHALL 暴露 `/run-bench`，供 master-controller 针对 target Service
endpoint 执行单个 benchmark case。

#### Scenario: 执行 benchmark case
- **WHEN** master-controller 调用 `/run-bench`，并传入 target endpoint、run ID、
  serve benchmark name、bench benchmark name、bench 参数和 output paths
- **THEN** bench-runner 针对 target endpoint 调用轻量 `vllm-project/vllm-bench`

#### Scenario: 拒绝 localhost target endpoint
- **WHEN** `/run-bench` 请求中的 target endpoint 指向 localhost 或 127.0.0.1
- **THEN** bench-runner 返回失败结果，避免误把自身当成 target vLLM 服务

#### Scenario: 返回结构化结果
- **WHEN** `vllm-bench` 执行完成
- **THEN** bench-runner 返回成功状态、exit code、raw output paths，以及 benchmark
  output 中可解析的 metric 字段

#### Scenario: 返回失败结果
- **WHEN** `vllm-bench` 执行失败或 timeout
- **THEN** bench-runner 返回失败状态、exit code 或 timeout reason，以及 raw log path

### Requirement: bench-runner 写入 raw outputs
bench-runner SHALL 将 raw benchmark JSON 和 raw logs 持久化到 run 结果目录。

#### Scenario: 持久化成功 raw output
- **WHEN** benchmark case 产生 JSON output
- **THEN** bench-runner 将其写入 `/results/{run_id}/raw_json/`

#### Scenario: 持久化进程 logs
- **WHEN** benchmark 进程产生 stdout 或 stderr
- **THEN** bench-runner 将日志写入 `/results/{run_id}/raw_logs/`

### Requirement: bench-runner 使用共享工作目录
bench-runner SHALL 能使用 Master Pod 共享挂载的 `/work` 目录存放临时协作文件。

#### Scenario: 使用 work 目录暂存中间文件
- **WHEN** benchmark 执行需要临时文件
- **THEN** bench-runner 将临时文件写入 `/work`，并不把它们当作最终结果文件

### Requirement: bench-runner 不申请国产卡资源
bench-runner SHALL 在不申请国产卡 accelerator 资源的情况下运行。

#### Scenario: bench-runner 容器资源
- **WHEN** 创建 Master Job Pod spec
- **THEN** bench-runner 容器没有 accelerator resource requests 或 limits

### Requirement: bench-runner 支持优雅 shutdown
bench-runner SHALL 暴露 `/shutdown`，让 master-controller 在全部压测完成后终止 agent。

#### Scenario: 请求 shutdown
- **WHEN** master-controller 调用 `POST /shutdown`
- **THEN** bench-runner 接受请求，并在当前响应返回后停止运行
