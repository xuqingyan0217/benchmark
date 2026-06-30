# vLLM Bench Platform

国产卡 vLLM 自动化压测平台 MVP。当前项目先实现一个能在 Kubernetes 单节点 smoke 环境里跑通的最小闭环：后端提交一次 run，Kubernetes 启动 Master Job，Master Job 动态拉起被测 vLLM Pod，bench-runner 对 target Service 发起 `vllm bench serve`，最后把 summary、raw log、失败 case 和 best config 写入结果目录。

## 当前架构

一次 run 主要分成三层：

1. `backend`
   - 读取本地 env 和矩阵配置，组装提交 payload。
   - 创建 Kubernetes 前置资源和 Master Job。
   - 查询 Job 状态、结果文件列表、失败 case。
   - 不直接执行 benchmark，也不直接创建 target vLLM Pod。

2. Master Job
   - 一个 Kubernetes Job，Pod 内有两个容器：`master-controller` 和 `bench-runner`。
   - 两个容器共享同一个 Pod 网络命名空间，所以 `master-controller` 通过 `localhost:18080` 调用 `bench-runner` agent。
   - 两个容器共享 `/configs`、`/results`、`/work` 三个挂载点。

3. target vLLM Pod
   - 由 `master-controller` 按每组 `serve_hparams` 动态创建。
   - 通过 Kubernetes Service 暴露给 `bench-runner`。
   - 只有 target Pod 申请国产卡/GPU 资源；Master Job 的两个控制面容器只申请 CPU/内存。

## 三个镜像

当前 smoke 配置里有三个镜像字段，都在 `configs/enving.env` 或 `configs/enving.example.env` 中配置：

- `MASTER_IMAGE`
  - 用于 Master Job 里的 `master-controller` 容器。
  - 对应 `docker/Dockerfile.master`。
  - 负责运行 `python3 -m vllm_bench_platform.master.master`，并通过 kubectl/RBAC 创建和清理 target Pod、Service。

- `BENCH_RUNNER_IMAGE`
  - 用于 Master Job 里的 `bench-runner` 容器。
  - 对应 `docker/Dockerfile.bench`。
  - 基础镜像当前来自 `vllm/vllm-openai:v0.8.5`，需要提供可用的 `vllm bench serve`。
  - 容器内运行轻量 HTTP agent，只监听 Pod 内部 `127.0.0.1:18080`。

- `TARGET_VLLM_IMAGE`
  - 用于被测的 target vLLM Pod。
  - 这个镜像才是真正加载模型、申请国产卡/GPU 资源、暴露 OpenAI-compatible API 的服务镜像。
  - 默认示例是 `vllm/vllm-openai:v0.8.5`，实际集群里应替换成 Kubernetes 节点可拉取、且适配对应硬件的镜像。

三个镜像的职责不能混用：`MASTER_IMAGE` 和 `BENCH_RUNNER_IMAGE` 属于控制面，`TARGET_VLLM_IMAGE` 属于被测服务。

## 挂载情况

Master Job 有三个主要挂载点：

- `/configs`
  - 来源：ConfigMap。
  - 只读输入，包含四个 JSON 文件：
    - `serve_hparams.json`
    - `bench_hparams.json`
    - `vendor_profile.json`
    - `model_config.json`
  - `master-controller` 启动后从这里读取本次 run 的矩阵、模型和厂商资源配置。

- `/results`
  - 来源：PVC。
  - 当前 MVP 使用 hostPath PV，宿主机路径由 `RESULTS_HOST_PATH` 指定，例如 `/tmp/vllm-bench-results`。
  - 所有最终结果都写到 `/results/{run_id}`。
  - 典型输出包括：
    - `summary.csv`
    - `summary.jsonl`
    - `failed_cases.jsonl`
    - `best_config.json`
    - `raw_logs/`
    - `raw_json/`
    - `server_logs/`
    - `events/`

- `/work`
  - 来源：emptyDir。
  - 只作为 Master Pod 内两个容器的临时协作目录。
  - 不作为最终 artifact，不应该依赖这里保存结果。

target vLLM Pod 另外有一个独立挂载：

- `/dev/shm`
  - 来源：memory emptyDir。
  - 大小由 `vendor_profile.shm_size` 控制，当前 env 组装默认是 `16Gi`。
  - 用于适配 vLLM 常见的 shared memory 需求。

## backend 的作用

`vllm_bench_platform/backend` 是当前 MVP 的后端提交和查询层。

提交时，backend 会：

1. 从 `configs/enving.env` 读取 namespace、三个镜像、target 资源名、模型路径、结果目录、timeout 等运行参数。
2. 从 `serve_hparams*.json` 和 `bench_hparams*.json` 读取压测矩阵。
3. 组装并校验 `SubmitJobRequest`。
4. 依次创建或提交：
   - Namespace
   - hostPath PV
   - RBAC
   - ConfigMap
   - PVC
   - Master Job

查询时，backend 会：

- 通过 `kubectl get job` 查询 Master Job 状态。
- 从 `RESULTS_HOST_PATH/{run_id}` 读取 summary 和 failed case。
- 列出结果目录下的文件。

backend 刻意不做这些事情：

- 不直接运行 `vllm bench serve`。
- 不直接创建 target Pod 或 target Service。
- 不等待整次压测完成。
- 不解析 raw log 里的完整细节。
- 不承担生产级认证、租户隔离或 HTTP API 框架职责。

这些边界是为了后续接入真实 API 服务时，可以复用当前 submit/query 语义，而不把 Kubernetes 执行逻辑散落到 Web 层。

## 配置文件

常用配置位于 `configs/`：

- `enving.example.env`
  - 本地 smoke 的 env 模板。
  - 复制为 `configs/enving.env` 后，按实际集群修改镜像、namespace、资源名和结果路径。

- `serve_hparams.json`
  - 完整服务端参数矩阵。
  - 每一项对应一个 target vLLM Pod 生命周期。

- `serve_hparams.smoke.json`
  - 更小的服务端 smoke 矩阵。

- `bench_hparams.json`
  - 完整请求压测矩阵。
  - 同一个 serve 配置下，不同 bench 配置会复用同一个 target Pod。

- `bench_hparams.smoke.json`
  - 更小的请求 smoke 矩阵。

- `model_config.example.json` / `vendor_profile.example.json`
  - 独立示例文件；当前 CLI smoke 主要通过 `enving.env` 组装这两部分 payload。

矩阵项需要包含 `_benchmark_name`，它会参与 case id、target 名称、日志文件命名和结果汇总。

## 本地 smoke 流程

1. 准备配置：

```powershell
Copy-Item configs\enving.example.env configs\enving.env
```

然后编辑 `configs/enving.env`，至少确认：

- `NAMESPACE`
- `MASTER_IMAGE`
- `BENCH_RUNNER_IMAGE`
- `TARGET_VLLM_IMAGE`
- `TARGET_RESOURCE_NAME`
- `TARGET_RESOURCE_COUNT`
- `MODEL_PATH`
- `RESULTS_HOST_PATH`

2. 构建控制面镜像：

```powershell
docker build -f docker\Dockerfile.master -t vllm-bench-platform/master:local .
docker build -f docker\Dockerfile.bench -t vllm-bench-platform/bench-runner:local .
```

如果 Kubernetes 节点无法直接读取本地 Docker 镜像，需要把镜像推送到集群可拉取的 registry，并同步修改 `configs/enving.env`。

3. 提交 smoke run：

```powershell
python -m vllm_bench_platform.backend.cli submit `
  --env configs\enving.env `
  --serve-configs configs\serve_hparams.smoke.json `
  --bench-configs configs\bench_hparams.smoke.json `
  --run-id run-001
```

4. 查看状态：

```powershell
python -m vllm_bench_platform.backend.cli status --env configs\enving.env --run-id run-001
```

5. 查看结果文件：

```powershell
python -m vllm_bench_platform.backend.cli results --env configs\enving.env --run-id run-001
python -m vllm_bench_platform.backend.cli failed-cases --env configs\enving.env --run-id run-001
```

## 结果目录

一次 run 完成后，结果会落在：

```text
{RESULTS_HOST_PATH}/{run_id}/
```

其中：

- `summary.csv`：人工查看的成功 case 汇总。
- `summary.jsonl`：程序消费的成功 case 汇总。
- `failed_cases.jsonl`：失败 case，包含错误类型、target pod、节点名、raw log 路径等。
- `best_config.json`：从成功 case 中选出的最佳配置。
- `raw_logs/`：每个 bench case 的原始 stdout/stderr。
- `raw_json/`：每个 bench case 的命令、exit code、metrics、stdout/stderr。
- `server_logs/`：每个 serve 配置对应的 target vLLM server log。
- `events/`：每个 serve 配置对应的 Kubernetes events。

## 后续TODO

### 待完成事项
1. bench_agent更换，从复用vllm大镜像的bench命令，到改为vllm-bench工具，以此来减轻container大小；
2. 后端逻辑微调，需生成更多的yaml文件到mainfest里面，包括configmap等，所有测试资源都在同一测试命名空间下，确保能够直接拿到backend提交后产出的yaml文件来直接放到k8s里面即可靠着已有镜像来完成远端测试；
3. gpu，tp，pp计算函数接入（reference/helper），使用动态资源计算来代替写死的配置；
4. pcv，包括其它持久化盘，都需挂载在同一目录下，方便后续同一删除。
5. 其它。

### 落地执行效果
将打好的镜像准备好，共计三个，一个用于master，一个用于bench工具，一个用于测试卡；

而后先在本地跑backend，之后把得到的yaml文件放到测试线，依次执行，完成测试。

### vllm-bench工具

#### 3.1 一键安装命令
```bash
curl -fsSL https://github.com/vllm-project/vllm-bench/releases/latest/download/vllm-bench-$(uname -m)-linux-musl -o vllm-bench && chmod +x vllm-bench
```

#### 3.2 基础压测执行示例
```bash
vllm-bench \
  --backend vllm \
  --base-url http://127.0.0.1:8000 \
  --model <model-name> \
  --dataset-name random \
  --random-input-len 1024 \
  --random-output-len 128 \
  --num-prompts 1000 \
  --max-concurrency 200
```

#### 3.3 扩展参数说明
- `--save-result`：输出压测结果至JSON文件
- `--dry-run`：仅生成、校验测试数据集，不发起推理请求

#### 详细文档
（reference/helper/README.md）
