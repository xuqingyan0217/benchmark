## ADDED Requirements

### Requirement: 后端接受 MVP 压测任务提交
后端 SHALL 提供 MVP submit-job API，用于接收单集群、单 namespace、单模型、单
vendor profile 的一次压测运行所需的四个配置文件。

#### Scenario: 提交合法 MVP run
- **WHEN** 请求包含 `serve_hparams.json`、`bench_hparams.json`、
  `vendor_profile.json`、`model_config.json`，且必填字段完整
- **THEN** 后端校验请求并返回生成的 `run_id`

#### Scenario: 拒绝缺失配置文件
- **WHEN** submit 请求缺少四个必需配置文件中的任意一个
- **THEN** 后端在创建 Kubernetes 资源前拒绝该请求

#### Scenario: 拒绝超出 MVP 的 fan-out
- **WHEN** submit 请求要求在一次 run 中使用多集群、多 namespace、多模型或多
  vendor profile
- **THEN** 后端以超出 MVP 范围为由拒绝该请求

### Requirement: 后端创建 run 所需 Kubernetes 资源
后端 SHALL 为已接受的压测运行创建启动所需的 ConfigMap、PVC 和 Master Job。

#### Scenario: 根据提交配置创建 ConfigMap
- **WHEN** run submit 请求被接受
- **THEN** 后端创建包含 `serve_hparams.json`、`bench_hparams.json`、
  `vendor_profile.json`、`model_config.json` 的 ConfigMap

#### Scenario: 创建结果 PVC
- **WHEN** run submit 请求被接受
- **THEN** 后端创建或引用一个可被 Master Pod 挂载到 `/results` 的 PVC

#### Scenario: 创建双容器 Master Job
- **WHEN** run submit 请求被接受
- **THEN** 后端创建 Master Job，且其 Pod template 包含 `master-controller` 和
  `bench-runner` 两个容器

### Requirement: Master 控制容器不申请国产卡资源
后端 SHALL 生成 Master Job，使 `master-controller` 和 `bench-runner` 都不申请国产卡
accelerator 资源。

#### Scenario: 校验 Master Job 资源申请
- **WHEN** 后端构建 Master Job manifest
- **THEN** Master Pod 的两个容器都不存在 accelerator resource requests

### Requirement: 后端返回 run 身份信息
后端 SHALL 返回足够的身份信息，让调用方能定位已创建的 run 资源。

#### Scenario: submit 响应包含资源身份
- **WHEN** run submit 请求被接受
- **THEN** 响应包含 `run_id`、namespace、ConfigMap 名称、PVC 名称和 Master Job 名称

### Requirement: 后端提供任务状态查询
后端 SHALL 提供按 `run_id` 查询 MVP 任务状态的接口。

#### Scenario: 查询已提交 run
- **WHEN** 调用方请求 `GET /api/runs/{run_id}`
- **THEN** 后端返回 run 状态、Master Job 名称、开始/结束时间、成功/失败统计和结果目录位置

#### Scenario: 查询不存在 run
- **WHEN** 调用方请求不存在的 `run_id`
- **THEN** 后端返回明确的 not found 响应，且不创建任何 Kubernetes 资源

### Requirement: 后端提供结果下载入口
后端 SHALL 提供按 `run_id` 查看或下载结果文件的接口。

#### Scenario: 查看结果文件列表
- **WHEN** 调用方请求 `GET /api/runs/{run_id}/results`
- **THEN** 后端返回 `/results/{run_id}/` 下的 MVP 结果文件列表或下载入口

### Requirement: 后端提供失败 case 查看
后端 SHALL 提供按 `run_id` 查看失败 case 摘要的接口。

#### Scenario: 查看 failed cases
- **WHEN** 调用方请求 `GET /api/runs/{run_id}/failed-cases`
- **THEN** 后端返回 `failed_cases.jsonl` 中的失败 case 摘要
