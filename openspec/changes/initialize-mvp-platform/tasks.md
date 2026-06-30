## 1. 项目骨架与共享契约

- [x] 1.1 创建已确认的源码目录和 packaging 文件，包括 `vllm_bench_platform/`、`configs/`、`manifests/`、`docker/`；验证除可导入包骨架外不加入运行时行为。覆盖全部 capabilities。
- [x] 1.2 实现 run 配置、serve configs、bench configs、vendor profile、model config 的共享 schema；校验必填字段和 `_benchmark_name`。覆盖 `job-submission` 和 `master-orchestration`。
- [x] 1.3 实现 MVP 标准失败类型枚举和 failed case schema，覆盖 benchmark、target、Kubernetes 和未知错误。覆盖 `result-management`。
- [x] 1.4 添加单元测试，覆盖合法配置、缺失配置文件、缺失 `_benchmark_name`、不支持的 MVP fan-out、失败类型枚举和 failed case 必填字段。覆盖 `job-submission`、`master-orchestration` 和 `result-management`。

## 2. 后端任务提交

- [x] 2.1 实现后端 `POST /api/runs` 请求/响应 schema 和输入校验；非法输入不得创建 Kubernetes 资源。覆盖 `job-submission`。
- [x] 2.2 实现 ConfigMap builder，写入四个必需配置文件；测试精确校验 key 名称。覆盖 `job-submission`。
- [x] 2.3 实现 PVC builder 和 Master Job builder；Master Job 必须包含 `master-controller` 和 `bench-runner` 两个容器。覆盖 `job-submission`。
- [x] 2.4 添加测试，证明 Master Job 的两个容器都不申请国产卡资源。覆盖 `job-submission` 和 `bench-runner-agent`。
- [x] 2.5 实现 Master Job 共享挂载配置，包含 `/configs`、`/results` 和 `/work`。覆盖 `job-submission`、`bench-runner-agent` 和 `master-orchestration`。
- [x] 2.6 实现 submit-job 编排，通过 Kubernetes client 抽象创建 ConfigMap、PVC、Master Job，并返回 run identity。覆盖 `job-submission`。
- [x] 2.7 实现 `GET /api/runs/{run_id}`、`GET /api/runs/{run_id}/results`、`GET /api/runs/{run_id}/failed-cases` 的 MVP 查询接口和测试。覆盖 `job-submission`。

## 3. Bench-Runner Agent

- [x] 3.1 实现 bench-runner HTTP agent，提供 `GET /health`、`POST /run-bench`、`POST /shutdown`。覆盖 `bench-runner-agent`。
- [x] 3.2 根据 `/run-bench` 请求字段和 bench 参数实现 vllm-bench 命令构造。覆盖 `bench-runner-agent`。
- [x] 3.3 实现 raw JSON 和 raw log 写入 `/results/{run_id}/raw_json/` 与 `/results/{run_id}/raw_logs/`。覆盖 `bench-runner-agent` 和 `result-management`。
- [x] 3.4 实现 `/work` 临时协作目录使用规则，确保临时文件不被当作最终结果。覆盖 `bench-runner-agent`。
- [x] 3.5 实现拒绝 localhost/127.0.0.1 target endpoint 的校验。覆盖 `bench-runner-agent`。
- [x] 3.6 实现结果解析，返回成功/失败、exit code 或 timeout reason、raw paths、可用 metrics。覆盖 `bench-runner-agent`。
- [x] 3.7 添加测试，覆盖 health、成功 run-bench、失败 run-bench、localhost target endpoint 拒绝、raw file 持久化、`/work` 临时文件和 shutdown 行为。覆盖 `bench-runner-agent`。

## 4. Master Controller 资源生命周期

- [x] 4.1 实现 matrix loader，读取挂载配置文件并构造 serve_config x bench_config case。覆盖 `master-orchestration`。
- [x] 4.2 实现 bench-runner client，针对 `localhost:18080` 调用 `/health`、`/run-bench`、`/shutdown`。覆盖 `master-orchestration` 和 `bench-runner-agent`。
- [x] 4.3 实现 target Pod builder，应用 vendor image、model config、serve args、resource name/count、env、node selector、tolerations、runtime class、shm size、port。覆盖 `master-orchestration`。
- [x] 4.4 添加测试，证明只有 target Pod 申请国产卡资源。覆盖 `master-orchestration`。
- [x] 4.5 实现 target Service builder，名称包含 `run_id` 和 serve benchmark name，并测试 selector、port 和 endpoint 连接关系。覆盖 `master-orchestration`。
- [x] 4.6 实现 Kubernetes client 抽象，支持创建、删除、强制删除、health 等待、抓取 logs、抓取 events。覆盖 `master-orchestration` 和 `result-management`。
- [x] 4.7 实现 RBAC manifests，包含 pods、pods/log、services、events、configmaps、jobs 的 MVP 最小权限，并添加结构校验测试。覆盖 `master-orchestration`。

## 5. Master Controller 执行循环

- [x] 5.1 实现启动流程：加载配置并等待 bench-runner `/health` 后再创建 target。覆盖 `master-orchestration`。
- [x] 5.2 实现每组 serve_config 的 target Pod/Service 创建，以及基于 `vendor_profile.health_path` 的 target health 等待。覆盖 `master-orchestration`。
- [x] 5.3 实现每组 bench_config 的调用循环；bench_config 变化时不得重建 target 资源。覆盖 `master-orchestration`。
- [x] 5.4 实现 benchmark case 失败重试一次，以及重试后仍失败按标准失败类型写入 failed case。覆盖 `result-management`。
- [x] 5.5 实现 target 启动失败时跳过当前 serve_config 下所有 bench_config。覆盖 `master-orchestration` 和 `result-management`。
- [ ] 5.6 实现 target 中途崩溃时记录失败、清理 target 并进入下一组 serve_config。覆盖 `master-orchestration` 和 `result-management`。
- [ ] 5.7 实现每组 serve_config 的 logs/events 抓取、target Service 删除、target Pod 删除、必要时强制删除、清理等待和 5 到 10 秒释放缓冲。覆盖 `master-orchestration` 和 `result-management`。
- [x] 5.8 实现最终 best_config 生成、bench-runner shutdown 调用和 Master Job 正常退出路径。覆盖 `master-orchestration` 和 `result-management`。

## 6. 结果管理

- [x] 6.1 实现结果目录初始化和 `run_meta.json` 写入。覆盖 `result-management`。
- [x] 6.2 实现成功 benchmark case 的 `summary.csv` 和 `summary.jsonl` 追加写入。覆盖 `result-management`。
- [x] 6.3 实现 `failed_cases.jsonl` 追加写入，包含 `run_id`、`case_id`、serve/bench identity、attempt、标准 `error_type`、`error_message`、`raw_log_path`、`target_pod_name`、`target_node_name`、`start_time`、`end_time`。覆盖 `result-management`。
- [x] 6.4 实现 target server log 和 event 文件写入。覆盖 `result-management`。
- [x] 6.5 实现确定性的 MVP `best_config.json` 生成，以及无成功 case 时的 fallback。覆盖 `result-management`。

## 7. Manifests、Dockerfiles 与示例配置

- [x] 7.1 添加 MVP 示例配置文件：serve hparams、bench hparams、vendor profile、model config。覆盖 `job-submission` 和 `master-orchestration`。
- [x] 7.2 添加基础 Kubernetes manifests：namespace、ServiceAccount、Role、RoleBinding、PVC、Master Job 示例；PVC 默认使用 ReadWriteOnce。覆盖 `job-submission` 和 `master-orchestration`。
- [x] 7.3 添加 master-controller 镜像 Dockerfile。覆盖 `master-orchestration`。
- [x] 7.4 添加 bench-runner 镜像 Dockerfile，明确轻量 vllm-bench 依赖策略。覆盖 `bench-runner-agent`。

## 8. 集成验证

- [x] 8.1 使用 fake Kubernetes client 和 fake bench-runner client 添加集成风格测试，覆盖完整 serve_config x bench_config 循环。覆盖 `master-orchestration` 和 `result-management`。
- [x] 8.2 添加测试，证明 bench_config 变化不会重建 target 资源。覆盖 `master-orchestration`。
- [x] 8.3 添加测试，证明 serve_config 变化会在创建下一个 target 前清理旧 target 资源。覆盖 `master-orchestration`。
- [ ] 8.4 添加小矩阵集成验证，覆盖 3 组 serve_config x 4 组 bench_config、失败重试、日志保存和 Pod 删除。覆盖 `master-orchestration` 和 `result-management`。
- [ ] 8.5 添加完整矩阵 dry-run 或 fake-client 验证，覆盖约 20 组 serve_config x 30 组 bench_config 的 600 行 summary 产出路径。覆盖全部 capabilities。
- [x] 8.6 添加验证命令或文档化 test target，用于运行 MVP 全部单元测试和集成测试。覆盖全部 capabilities。
