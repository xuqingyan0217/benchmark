# vLLM Bench Platform

国产卡 / GPU 环境下的 vLLM 自动化压测 MVP。当前目标是：本地 backend 只负责生成可搬运的 Kubernetes YAML，把 YAML 拷到测试线后按顺序 apply，由集群里的 Master Job 完成 target vLLM Pod 的创建、压测、日志抓取和结果落盘。

## 当前架构

一次 run 分成三层：

1. `backend`
   - 读取 `configs/enving.env`、`serve_hparams*.json`、`bench_hparams*.json`。
   - 生成或提交 Namespace、RBAC、PV、PVC、ConfigMap、Master Job。
   - 查询 Master Job 状态、结果文件列表和 failed cases。
   - 不直接执行 benchmark，也不直接创建 target vLLM Pod。

2. Master Job
   - Kubernetes Job，Pod 内只有一个容器：`master-controller`。
   - 读取 `/configs` 中的四个 JSON 配置文件。
   - 通过 Hugging Face API 读取模型 config 和权重文件大小，计算 GPU 数量、TP、PP。
   - 按每组 `serve_hparams` 创建 target vLLM Pod 和 Service。
   - 等待 target ready / health 成功后，直接调用容器内的 `vllm-bench`。
   - 抓取 target logs / events，清理 target Service / Pod，写入结果。
   - 当前通过 `kubectl` 操作 Kubernetes API，后续可替换为 Kubernetes Python client。

3. target vLLM Pod
   - 由 `master-controller` 动态创建。
   - 使用 `TARGET_VLLM_IMAGE` 启动 vLLM OpenAI-compatible server。
   - 只有 target Pod 申请 GPU / 国产卡资源；Master 容器只申请 CPU / 内存。
   - 通过 Kubernetes Service 暴露给 `vllm-bench`。

## 镜像

当前架构只需要两个镜像：

- `MASTER_IMAGE`
  - 用于 Master Job 的 `master-controller` 容器。
  - 对应 `docker/Dockerfile.master`。
  - 包含平台 Python 代码、`kubectl`、`vllm-bench`。
  - 不加载模型，不申请 GPU。

- `TARGET_VLLM_IMAGE`
  - 用于被测的 target vLLM Pod。
  - 真正加载模型、申请 GPU / 国产卡资源、暴露 OpenAI-compatible API。
  - 示例值是 `vllm/vllm-openai:v0.8.5`；真实测试线应替换成集群可拉取、且适配硬件的镜像。

旧的 `BENCH_RUNNER_IMAGE`、bench-runner 容器、`bench_agent.py` 和 `localhost:18080` agent 通信路径已经移除。

## 挂载

Master Job 挂载：

- `/configs`
  - 来源：ConfigMap。
  - 只读输入，包含：
    - `serve_hparams.json`
    - `bench_hparams.json`
    - `vendor_profile.json`
    - `model_config.json`

- `/results/{run_id}`
  - 来源：PVC。
  - PVC 对应的 hostPath 为：

    ```text
    {PERSIST_ROOT}/{namespace}/{run_id}
    ```

  - Master 代码仍以 `/results/{run_id}` 写入结果，实际落到上面的 run 根目录。

- `/work`
  - 来源：emptyDir。
  - 仅用于 `vllm-bench` 临时执行目录，不作为最终 artifact。

target vLLM Pod 挂载：

- `/dev/shm`
  - 来源：memory emptyDir。
  - 大小由 `vendor_profile.shm_size` 控制，默认 `16Gi`。

- `MODEL_HOST_PATH -> MODEL_MOUNT_PATH`
  - 可选 hostPath，只读挂载完整模型目录。
  - 如果启用，应把 `MODEL_PATH` 设置为 target 容器内可见的 `MODEL_MOUNT_PATH` 或其子目录。

- `MODEL_CACHE_HOST_PATH -> MODEL_CACHE_MOUNT_PATH`
  - 可选 hostPath，用作 Hugging Face 下载缓存。
  - target 容器会把 `HF_HOME` 和 `HUGGINGFACE_HUB_CACHE` 指向 `MODEL_CACHE_MOUNT_PATH`。

## 资源配置

GPU 数量、TP、PP 直接由 env 手工填写，backend 会写入 `vendor_profile.json`，Master 创建 target Pod 时原样使用，不访问 Hugging Face，也不做自动估算。

关键字段：

- `TARGET_RESOURCE_COUNT`：target Pod 申请的 GPU / 国产卡数量。
- `TENSOR_PARALLEL_SIZE`：注入 vLLM 的 `--tensor-parallel-size`。
- `PIPELINE_PARALLEL_SIZE`：注入 vLLM 的 `--pipeline-parallel-size`。

约束：

- `TENSOR_PARALLEL_SIZE * PIPELINE_PARALLEL_SIZE == TARGET_RESOURCE_COUNT`
- TP 是否能整除模型 attention heads 由用户自行保证；填错时 vLLM 启动阶段会失败，Master 会记录 target 失败和 events。

## 模型加载和下载

真正加载模型的是 target vLLM Pod，不是 backend，也不是 Master Pod。

`MODEL_PATH` 会作为 vLLM 的 `--model` 参数传给 target 容器：

- 如果 `MODEL_PATH` 是容器内已有路径，vLLM 从本地加载。
- 如果 `MODEL_PATH` 是 Hugging Face repo id，例如 `Qwen/Qwen2.5-0.5B-Instruct`，vLLM 可能在 target 容器启动时下载模型。

当前支持给 target Pod 单独挂载持久化模型目录或 Hugging Face cache。因此：

- 模型已经在 target 镜像内：不会重新下载。
- 模型路径是 target 容器可见的本地/共享挂载路径：不会重新下载。
- 依赖 Hugging Face 在线下载，且配置了 `MODEL_CACHE_HOST_PATH`：首次下载会落到宿主机 cache 目录，后续调度到同一节点并挂同一目录的 target Pod 会复用缓存。
- 依赖 Hugging Face 在线下载，但没有配置持久化 cache：每次新 target Pod 都可能重新下载。

由于每组 `serve_hparams` 会创建一个新的 target Pod，如果没有模型缓存，完整矩阵可能会触发多次下载。真实测试建议提前把模型放到 target 镜像、节点本地模型目录，或配置 `MODEL_CACHE_HOST_PATH`。

## 持久化目录

统一根路径：

```env
PERSIST_ROOT=/tmp/vllm-bench
```

一次 run 的宿主机目录：

```text
{PERSIST_ROOT}/{namespace}/{run_id}
```

例如：

```text
/tmp/vllm-bench/bench/run-001
```

典型输出：

- `summary.csv`
- `summary.jsonl`
- `failed_cases.jsonl`
- `best_config.json`
- `raw_logs/`
- `raw_json/`
- `server_logs/`
- `events/`

后续 cache、model、artifacts 等持久化内容也应归到同一个 run 目录树下，方便删除一次 run 时只清理一棵目录和一组 Kubernetes 资源。

## 配置文件

常用配置位于 `configs/`：

- `enving.example.env`
  - 本地 smoke env 模板。
  - 复制为 `configs/enving.env` 后按实际集群修改。

- `serve_hparams.json`
  - 完整服务端参数矩阵。
  - 每一项对应一个 target vLLM Pod 生命周期。

- `serve_hparams.smoke.json`
  - 更小的服务端 smoke 矩阵。

- `bench_hparams.json`
  - 完整请求压测矩阵。
  - 同一个 serve 配置下，不同 bench 配置复用同一个 target Pod。

- `bench_hparams.smoke.json`
  - 更小的请求 smoke 矩阵。

关键 env：

```env
NAMESPACE=bench
MASTER_IMAGE=vllm-bench-platform/master:local
TARGET_VLLM_IMAGE=vllm/vllm-openai:v0.8.5
TARGET_RESOURCE_NAME=nvidia.com/gpu
TARGET_RESOURCE_COUNT=1
TENSOR_PARALLEL_SIZE=1
PIPELINE_PARALLEL_SIZE=1
MODEL_PATH=Qwen/Qwen2.5-0.5B-Instruct
MODEL_NAME=Qwen2.5-0.5B-Instruct
SERVED_MODEL_NAME=Qwen2.5-0.5B-Instruct
DTYPE=float16
MODEL_HOST_PATH=
MODEL_MOUNT_PATH=
MODEL_CACHE_HOST_PATH=/tmp/vllm-bench/model-cache
MODEL_CACHE_MOUNT_PATH=/root/.cache/huggingface
PERSIST_ROOT=/tmp/vllm-bench
BENCH_BINARY=vllm-bench
BENCH_TIMEOUT_SECONDS=600
BENCH_NUM_PROMPTS=2
```

## 本地 render 流程

准备 env：

```powershell
Copy-Item configs\enving.example.env configs\enving.env
```

构建 Master 镜像：

```powershell
docker build -f docker\Dockerfile.master -t vllm-bench-platform/master:local .
```

生成 YAML：

```powershell
python -m vllm_bench_platform.backend.cli render `
  --env configs\enving.env `
  --serve-configs configs\serve_hparams.smoke.json `
  --bench-configs configs\bench_hparams.smoke.json `
  --run-id run-001
```

默认输出：

```text
manifests/generated/run-001/
```

生成文件按 apply 顺序编号：

- `00-namespace.yaml`
- `01-rbac.yaml`
- `02-pv.yaml`
- `03-pvc.yaml`
- `04-configmap.yaml`
- `05-master-job.yaml`

拷到测试线后按顺序执行：

```bash
kubectl apply -f 00-namespace.yaml
kubectl apply -f 01-rbac.yaml
kubectl apply -f 02-pv.yaml
kubectl apply -f 03-pvc.yaml
kubectl apply -f 04-configmap.yaml
kubectl apply -f 05-master-job.yaml
```

如果本机 kube-context 可直接访问测试集群，也可以提交：

```powershell
python -m vllm_bench_platform.backend.cli submit `
  --env configs\enving.env `
  --serve-configs configs\serve_hparams.smoke.json `
  --bench-configs configs\bench_hparams.smoke.json `
  --run-id run-001
```

查询：

```powershell
python -m vllm_bench_platform.backend.cli status --env configs\enving.env --run-id run-001
python -m vllm_bench_platform.backend.cli results --env configs\enving.env --run-id run-001
python -m vllm_bench_platform.backend.cli failed-cases --env configs\enving.env --run-id run-001
```

## 本地单卡 smoke 链路示例

假设本地只有一张 8GB GPU，使用当前 smoke 配置：

1. backend 本地生成 YAML。
2. PV hostPath 指向：

   ```text
   /tmp/vllm-bench/bench/run-001
   ```

3. Master Job 启动，不申请 GPU。
4. Master 使用 env 中的 `TARGET_RESOURCE_COUNT=1`、`TENSOR_PARALLEL_SIZE=1`、`PIPELINE_PARALLEL_SIZE=1`。
5. Master 创建一个 target vLLM Pod，请求：

   ```yaml
   nvidia.com/gpu: 1
   ```

6. vLLM 启动参数包含：

   ```text
   --tensor-parallel-size 1
   --pipeline-parallel-size 1
   ```

7. target Pod 加载 `MODEL_PATH` 指定的模型。
8. target Service ready 后，Master 调用 `vllm-bench`。
9. 压测结果写到：

   ```text
   /tmp/vllm-bench/bench/run-001
   ```

如果手工配置的 `TARGET_RESOURCE_COUNT` 超过集群可用资源，target Pod 会因为资源不足 Pending，Master 会记录失败 case 和 events。

## 错误和等待行为

当前没有给 target Pod 配置 Kubernetes 层面的过期时间，也没有给 Pod 设置自动 TTL。target Pod 不会因为“模型下载耗时很久”或“整体运行时间很久”被 Kubernetes 自动删除。

Master 仍然有程序内部等待边界：

- 等 target Pod ready：默认 `600s`。
- 等 target HTTP health：默认 `600s`。
- 单次 `vllm-bench`：由 `BENCH_TIMEOUT_SECONDS` 控制。
- 等 target Pod 删除：默认 `120s`，超时后尝试 force delete。

这些等待不是 Pod 生命周期 TTL，而是 Master 判断某个阶段是否继续等待的上限。若超时，Master 会记录失败并主动清理 target Pod / Service。

对于不可恢复的 target 容器错误，Master 不再等待 ready timeout。一旦轮询到以下 container reason，会立即判定该轮失败，写入 `failed_cases.jsonl`，抓取 logs/events，删除 target Pod / Service，然后进入下一组 `serve_hparams`：

- `OOMKilled`
- `Error`
- `RunContainerError`
- `CrashLoopBackOff`
- `ImagePullBackOff`
- `ErrImagePull`
- `InvalidImageName`
- `CreateContainerConfigError`
- `CreateContainerError`

例如显存不足导致 target Pod 变成 `Error` / `OOMKilled` 时，失败信息会类似：

```text
target pod failed before ready: OOMKilled
```

如果 target Pod 只是正常下载模型、初始化 vLLM，且没有出现上述 fatal reason，Master 会继续等待，直到 ready / health 成功或达到内部等待上限。


## 后续 TODO（已完成）

详细执行计划见 `docs/implementation-plan.md`。

## 额外 TODO

1. 模型挂载当前是hostpath，pod跨节点就无效了，最终需要使用nfs，pvc
2. 当前直接测试的时候，有时会遇到显存顶不住的情况，导致任务直接失败，能否按照下面流程进行优化：
```
读取 GPU 信息
    ↓
读取模型信息
    ↓
预估：模型权重 + KV Cache + 运行开销 是否能放下
    ↓
如果不够，自动降低参数
    ↓
启动 vLLM serve
    ↓
确认 /v1/models 健康
    ↓
再执行 vllm bench serve
```
3. 获取指标信息，显存，能看到执行时的指标

---
1. target pod error后没被回收，依旧10分钟超时回收
2. 打印一个标记，确保模型挂载是生效了的
