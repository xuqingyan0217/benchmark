# vLLM Bench Platform 后续实施计划

本文用于跟踪 README 后续 TODO 的落地。原则：按新要求直接改造，不做旧版双容器 bench-agent 架构的向后兼容。

## 总体目标

新执行链路：

```text
本地 backend render -> 生成完整 YAML -> 远端 kubectl apply -> Master Job 单容器
  -> master-controller 创建 target Pod/Service
  -> master-controller 直接调用 vllm-bench 二进制
  -> 结果写入统一持久化目录
```

最终镜像：

- `MASTER_IMAGE`：包含平台 master 代码、Kubernetes 操作能力、`vllm-bench` 二进制。
- `TARGET_VLLM_IMAGE`：被测 vLLM 服务镜像，申请测试卡资源。

不再保留：

- `BENCH_RUNNER_IMAGE`
- `bench-runner` 容器
- `bench_agent.py` HTTP agent
- `localhost:18080` agent 通信
- 旧 `vllm bench serve` Python CLI 调用路径
- `MODEL_METADATA_HOST_PATH`
- `/model-metadata` Master 挂载
- `configs/model_metadata.example/`

## 关于 Master 镜像为什么还有 kubectl

Master 容器运行时仍然需要操作 Kubernetes API，因为它负责：

- 按每组 `serve_hparams` 创建 target vLLM Pod。
- 创建 target Service。
- 等待 target Pod ready。
- 访问 target health endpoint。
- 抓取 target logs 和 events。
- 删除 target Service/Pod。

因此 Master 需要的是“Kubernetes API 操作能力”，不是必须依赖 `kubectl` 本身。

当前项目的 `KubectlMasterClient` 已经用 `kubectl apply/get/delete/logs/wait` 跑通最小闭环，所以第一阶段继续保留 `kubectl`，以降低本轮架构切换风险。后续可以单独做一批，把它替换为：

- Kubernetes Python client；或
- in-cluster service account token + Kubernetes REST API。

替换标准：上层 `master.master` 的编排语义不变，只替换 `k8s_client` 实现。

## 分批计划

### 第 1 批：Master 单容器架构

目标：Master Job 只包含 `master-controller` 一个容器，bench 执行逻辑并入 master。

任务：

- [x] 删除 `MasterJobOptions.bench_runner_image`。
- [x] 删除 `BENCH_RUNNER_IMAGE` 相关 env 读取与文档。
- [x] 删除 bench-runner memory request/limit 配置。
- [x] 删除 bench-runner health/request timeout 配置。
- [x] `build_master_job` 只生成 `master-controller` 容器。
- [x] Master Job 继续挂载 `/configs`、`/results`、`/work`。
- [x] Master 容器不申请测试卡资源。
- [x] 更新相关单元测试。

验收：

- Master Job manifest 只有一个 container。
- 代码中无 `localhost:18080` 执行依赖。
- 代码中无 `BENCH_RUNNER_IMAGE` 配置依赖。

### 第 2 批：master 直接调用 vllm-bench

目标：master-controller 直接执行 `vllm-bench` 二进制并写结果。

任务：

- [x] 将 `run_bench_case` 逻辑迁移到 master 可直接调用的模块。
- [x] 命令从 `vllm bench serve` 改为 `vllm-bench`。
- [x] 使用 `--backend vllm`、`--base-url <target-service>`、`--model <served-model-name>`。
- [x] 支持 `--save-result` 和明确的 result filename。
- [x] raw log 写入 `/results/{run_id}/raw_logs/`。
- [x] raw json 写入 `/results/{run_id}/raw_json/`。
- [x] 失败、超时、结果缺失都写入结构化失败信息。
- [x] 删除 `bench_agent.py` 和 `master/bench_client.py`。

验收：

- 成功 case 写 summary。
- 失败 case 最多重试一次，最终写 `failed_cases.jsonl`。
- `vllm-bench` 命令可在测试中注入 fake runner 验证。

### 第 3 批：backend 生成完整 YAML

目标：本地 backend 只负责生成可搬运 YAML，不要求本地能访问远端 Kubernetes。

任务：

- [x] 新增 `render` 命令。
- [x] 输出目录默认 `manifests/generated/{run_id}/`。
- [x] 生成完整 apply 顺序：
  - [x] `00-namespace.yaml`
  - [x] `01-rbac.yaml`
  - [x] `02-pv.yaml`
  - [x] `03-pvc.yaml`
  - [x] `04-configmap.yaml`
  - [x] `05-master-job.yaml`
- [x] 所有资源使用同一个测试 namespace。
- [x] 保留查询命令，但提交命令后续可弱化或删除。

验收：

- 本地执行 backend 后拿到完整 YAML。
- YAML 拷到测试线后可按顺序 apply。
- ConfigMap 内容包含本次 run 所有配置。

### 第 4 批：GPU / TP / PP 手工配置

任务：

- [x] 删除 `MODEL_METADATA_HOST_PATH` 配置。
- [x] 删除 Master Job 的 `/model-metadata` hostPath 挂载。
- [x] 删除 `configs/model_metadata.example/`。
- [x] 删除 Hugging Face 元数据访问和自动资源规划。
- [x] 删除 `resource_planner.py`。
- [x] 使用 `TARGET_RESOURCE_COUNT` 手工配置 target Pod 申请的 GPU / 国产卡数量。
- [x] 使用 `TENSOR_PARALLEL_SIZE` 手工配置 TP。
- [x] 使用 `PIPELINE_PARALLEL_SIZE` 手工配置 PP。
- [x] backend 将上述三个 env 写入 `vendor_profile.json`。
- [x] Master 原样使用 `vendor_profile` 中的资源数量和 TP/PP。
- [x] vLLM serve args 注入 `--tensor-parallel-size` 和 `--pipeline-parallel-size`。

验收：

- `TENSOR_PARALLEL_SIZE * PIPELINE_PARALLEL_SIZE == TARGET_RESOURCE_COUNT`。
- Master Job 不再访问 Hugging Face。
- Master Job manifest 不再包含 `TARGET_GPU_MEMORY_GB`、`HF_ENDPOINT`、`HF_TOKEN`。
- TP 是否能整除 attention heads 由用户自行保证；填错时由 vLLM 启动失败暴露。

### 第 5 批：target 模型和 Hugging Face cache 挂载

目标：避免每组 `serve_hparams` 创建新 target Pod 时重复下载模型，同时支持节点本地模型目录。

任务：

- [x] 在 `ModelConfig` 中加入 `model_host_path` 和 `model_mount_path`。
- [x] 在 target Pod 中将 `MODEL_HOST_PATH` 只读挂载到 `MODEL_MOUNT_PATH`。
- [x] 在 `ModelConfig` 中加入 `model_cache_host_path` 和 `model_cache_mount_path`。
- [x] 在 target Pod 中将 `MODEL_CACHE_HOST_PATH` 挂载到 `MODEL_CACHE_MOUNT_PATH`。
- [x] target 容器内设置 `HF_HOME=MODEL_CACHE_MOUNT_PATH`。
- [x] target 容器内设置 `HUGGINGFACE_HUB_CACHE=MODEL_CACHE_MOUNT_PATH`。
- [x] 更新 `configs/enving.example.env`，为每个 env 项补充说明。

验收：

- 不配置模型目录时，target Pod 可通过 Hugging Face 下载模型。
- 配置 `MODEL_CACHE_HOST_PATH` 后，首次下载会落到宿主机 cache 目录。
- 后续调度到同一节点并挂同一 cache 目录的 target Pod 会复用模型缓存。
- 配置 `MODEL_HOST_PATH` 和 `MODEL_MOUNT_PATH` 后，vLLM 可直接从本地模型目录加载。

### 第 6 批：统一持久化目录

目标：PVC、hostPath 和后续持久化盘都归到同一个根目录，方便统一删除。

任务：

- [x] 引入统一根路径 `PERSIST_ROOT`。
- [x] 每次 run 使用 `{PERSIST_ROOT}/{namespace}/{run_id}/`。
- [x] results 挂载和查询路径统一到该 run 根目录下。
- [x] 后续 cache/model/artifacts 也放在同一 run 根目录。
- [x] 更新 README 和排障说明。

验收：

- 删除一个 run 只需要删除一个目录树和一组 Kubernetes 资源。
- 结果查询不再依赖零散路径。

### 第 7 批：错误处理和等待策略

目标：target Pod 出现明确不可恢复错误时及时失败、落盘和清理，不再无效等待 ready timeout；同时明确 Pod 生命周期和 Master 内部等待的边界。

任务：

- [x] 明确 target Pod 不配置 Kubernetes TTL 或 `activeDeadlineSeconds`。
- [x] 明确模型下载耗时或整体运行耗时不会让 Kubernetes 自动删除 target Pod。
- [x] 在 `wait_pod_ready()` 中检查 container waiting / terminated reason。
- [x] 遇到 `OOMKilled` 立即返回失败。
- [x] 遇到 `Error` 或 `RunContainerError` 立即返回失败。
- [x] 遇到 `CrashLoopBackOff` 立即返回失败。
- [x] 遇到 `ImagePullBackOff`、`ErrImagePull`、`InvalidImageName` 立即返回失败。
- [x] 遇到 `CreateContainerConfigError`、`CreateContainerError` 立即返回失败。
- [x] failed case 的错误信息包含具体 container reason。
- [x] fatal reason 出现后立即抓取 logs/events、删除 target Pod/Service，并进入下一组 `serve_hparams`。
- [ ] 将 target ready timeout 配置化。
- [ ] 将 target health timeout 配置化。
- [ ] 支持 timeout 为 `0` 表示只因 fatal reason 失败，不按时间上限删除正在正常启动/下载的 target Pod。

验收：

- 显存不足导致 `OOMKilled` 时，不再等待默认 `600s` ready timeout。
- `failed_cases.jsonl` 中能看到具体错误原因，例如 `target pod failed before ready: OOMKilled`。
- target Pod 的删除仍由 Master 主动执行，而不是依赖 Kubernetes 自动过期。

## 当前执行顺序

1. 第 1 批和第 2 批一起落地，因为它们是同一个架构拐点。
2. 跑通测试后，再进入第 3 批 YAML 生成。
3. 第 4 批手工资源配置依赖新的 target args 注入点。
4. 第 5 批模型/cache 挂载解决 target Pod 反复下载模型的问题。
5. 第 6 批统一持久化目录作为收口。
6. 第 7 批错误处理和等待策略用于减少无效等待，并保护长时间模型下载场景。
