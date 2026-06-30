# Codex Agent 指令

## 项目

项目名称：国产卡 vLLM 自动化压测平台。

本项目用于在 Kubernetes 环境中，对运行在国产 AI 加速卡上的 OpenAI-compatible
vLLM 服务进行自动化压测。平台需要根据不同 vendor profile、vLLM 镜像、模型配置、
serve 参数和 bench 参数，自动启动 target vLLM 服务、执行压测、收集结果、保存日志
和事件，并分析最优配置。

## 开发规则

1. 严格遵守 Spec-first 开发方式。
2. 用户确认相关 OpenSpec proposal、design、specs、tasks 之前，不允许写业务代码。
3. 每次新增能力、修改能力、重构能力，都必须先创建 OpenSpec change。
4. 每个功能必须明确记录：
   - 背景
   - 目标
   - 非目标
   - 用户故事
   - 约束条件
   - 方案设计
   - 数据结构
   - 接口设计
   - 任务拆分
   - 验收标准
   - 风险点
5. 每一步实现都必须能回溯到已确认的 OpenSpec change。
6. 重要决策、架构约束、目录结构、命名规范、阶段性结论必须记录到项目文档。
7. 优先完成 MVP。未经 OpenSpec change 确认，不允许擅自扩大范围。
8. 代码必须工程化、可维护、可扩展，不能堆临时脚本。
9. 本项目文档、OpenSpec 说明、任务拆分和面向用户的说明默认使用中文。
10. `vllm_bench_platform/**/*.py` 源码整体注释率不得低于 40%；注释必须解释业务意图、
    OpenSpec 约束、Kubernetes 资源设计、失败处理或维护注意点，不能只机械复述代码。

## MVP 架构

已确认的 MVP 架构：

```text
frontend
  -> backend
  -> Master Job
  -> Master Pod
       container-1: master-controller
       container-2: bench-runner
```

`master-controller` 职责：

- 读取运行配置。
- 创建和删除 target vLLM Pod。
- 创建和删除 target Service。
- 等待 target health。
- 控制 serve_config x bench_config 循环。
- 通过 `localhost:18080` 调用 bench-runner。
- 抓取 target Pod 日志和 events。
- 写入 summary、失败记录和分析结果。

`bench-runner` 职责：

- 在 `localhost:18080` 启动本地 HTTP agent。
- 调用轻量 `vllm-project/vllm-bench`。
- 保存 raw JSON 和 raw logs。
- 返回结构化压测结果。
- 在运行结束后支持 shutdown。

target vLLM Pod：

- 运行国产卡 vLLM 镜像。
- 申请 vendor accelerator 资源，例如 `vendor.com/xpu`、Ascend、MLU、
  MThreads 或配置中的其他资源名。
- 暴露 OpenAI-compatible API。
- 不直接写压测结果。

## 核心规则

1. `bench_config` 变化时，不重启 target vLLM Pod。
2. `serve_config` 变化时，必须删除旧 target Service 和 Pod，再创建新的 target
   Service 和 Pod。
3. `bench-runner` 和 `master-controller` 在同一个 Master Pod 内通过
   `localhost:18080` 通信。
4. `bench-runner` 使用轻量 `vllm-project/vllm-bench`，不是完整 Python vLLM
   运行环境。
5. `bench-runner` 不申请 accelerator 资源。
6. `master-controller` 不申请 accelerator 资源。
7. 只有 target vLLM Pod 申请 accelerator 资源。
8. target vLLM Pod 日志和 Kubernetes events 由 `master-controller` 抓取并写入
   PVC。
9. MVP 只支持单集群、单 namespace、单模型、单 vendor profile。
10. bench-runner 不能通过 localhost 或 Pod IP 访问 target vLLM，必须通过 target
    Service 访问。
11. Master Pod 内两个容器共享 `/configs`、`/results` 和 `/work`。
12. MVP 阶段 PVC 可以使用 ReadWriteOnce；多 Worker Pod 并发写结果时再评估
    ReadWriteMany 或对象存储。

## MVP 范围

MVP 包含：

- 后端提交任务接口。
- 后端任务状态查询接口。
- 后端结果下载接口。
- 后端失败 case 查看接口。
- 后端生成 ConfigMap、PVC、Master Job。
- 双容器 Master Pod。
- master-controller 读取配置。
- bench-runner 本地 HTTP agent。
- bench-runner `/health`、`/run-bench`、`/shutdown`。
- target vLLM Pod 和 Service 生命周期管理。
- target `/health` readiness 等待。
- vllm-bench 调用。
- raw JSON 和 raw log 持久化。
- `summary.csv`、`summary.jsonl`、`failed_cases.jsonl`、`best_config.json`、
  `run_meta.json`。
- 失败 bench case 重试一次。
- 每组 serve_config 结束后清理 target Service 和 target Pod。
- ServiceAccount、Role、RoleBinding 的最小 RBAC 权限。

MVP 不包含：

- 复杂前端页面。
- 多集群。
- 多 vendor 并发。
- 多模型并发。
- Prometheus 指标采集。
- Grafana 看板。
- Kueue 或 Volcano。
- WebSocket 实时日志。
- 历史结果数据库。
- 高级 Pareto 分析。

## 必需配置文件

- `serve_hparams.json`
- `bench_hparams.json`
- `vendor_profile.json`
- `model_config.json`

`serve_hparams.json` 和 `bench_hparams.json` 的每一项都必须包含 `_benchmark_name`。

## 必需结果目录

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

## 推荐源码目录

相关 spec 和 tasks 确认前，不要创建这些业务代码目录。

```text
vllm_bench_platform/
  backend/
    api.py
    submit_job.py
    config_builder.py
    job_builder.py
    schemas.py
  master/
    master.py
    matrix_loader.py
    k8s_client.py
    target_pod_builder.py
    service_builder.py
    bench_client.py
    result_writer.py
    analyzer.py
    schemas.py
  bench_runner/
    bench_agent.py
    vllm_bench_runner.py
    result_parser.py
    schemas.py
configs/
  serve_hparams.json
  bench_hparams.json
  vendor_profile.json
  model_config.json
manifests/
  namespace.yaml
  rbac.yaml
  pvc.yaml
  master_job.yaml
docker/
  Dockerfile.master
  Dockerfile.bench
```

## 阶段性方案结论

MVP 主方案已经封存为：

- 后端创建 Master Job。
- Master Job 启动双容器 Master Pod。
- Master Pod 内 master-controller 负责全局调度。
- Master Pod 内 bench-runner 使用轻量 vllm-bench 执行压测。
- master-controller 动态创建国产 vLLM Pod + Service。
- 每组 serve_config 重启一次国产 vLLM Pod。
- 同一 serve_config 下连续执行多组 bench_config。
- 结果统一写入 PVC。

明确不采用：

- 600 个轻量 Worker Job 作为 MVP 主方案。
- 单容器重 Job 大脚本作为 MVP 主方案。

MVP 失败类型包括：

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
