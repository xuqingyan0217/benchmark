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
   - 读取 `/model-metadata` 中的模型 metadata，计算 GPU 数量、TP、PP。
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

- `/model-metadata`
  - 来源：`MODEL_METADATA_HOST_PATH` hostPath，只读挂载。
  - 只应包含轻量 metadata：
    - `config.json`
    - `model.safetensors.index.json`
  - 不要指向完整模型权重目录。挂载不会把文件复制进 Master 镜像，但挂完整模型目录会让 Master 调度依赖模型所在节点，也会混淆职责边界。

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

## 资源规划

`TARGET_RESOURCE_COUNT` 不再手工填写。

资源规划发生在 Master Pod 内：

1. `master-controller` 启动。
2. 读取 `/configs` 生成 `RunConfig`。
3. 读取 `/model-metadata/config.json` 获取 `num_attention_heads`。
4. 读取 `/model-metadata/model.safetensors.index.json` 获取模型权重大小。
5. 根据 `TARGET_GPU_MEMORY_GB` 估算 GPU 数量。
6. 根据 GPU 数量和 attention heads 计算：
   - `--tensor-parallel-size`
   - `--pipeline-parallel-size`
7. 创建 target vLLM Pod 时注入 GPU 资源数和 TP / PP 参数。

当前估算规则在 `vllm_bench_platform/resource_planner.py`：

- `estimated_vram_gb = model_weight_size_gb * 1.8`
- `gpu_count = ceil(estimated_vram_gb / TARGET_GPU_MEMORY_GB)`
- 如果多卡且 GPU 数量为奇数，则向上补成偶数。
- TP 选择能整除 `gpu_count` 且能整除 `num_attention_heads` 的最大值。
- `PP = gpu_count / TP`

约束：

- `TP * PP == GPU_COUNT`
- `num_attention_heads % TP == 0`
- 缺少必要 metadata 时，Master Job 会直接失败。

## 模型加载和下载

真正加载模型的是 target vLLM Pod，不是 backend，也不是 Master Pod。

`MODEL_PATH` 会作为 vLLM 的 `--model` 参数传给 target 容器：

- 如果 `MODEL_PATH` 是容器内已有路径，vLLM 从本地加载。
- 如果 `MODEL_PATH` 是 Hugging Face repo id，例如 `Qwen/Qwen2.5-0.5B-Instruct`，vLLM 可能在 target 容器启动时下载模型。

当前 MVP 没有给 target Pod 单独挂载持久化模型缓存。因此：

- 模型已经在 target 镜像内：不会重新下载。
- 模型路径是 target 容器可见的本地/共享挂载路径：不会重新下载。
- 依赖 Hugging Face 在线下载，且没有持久化 cache：每次新 target Pod 都可能重新下载。

由于每组 `serve_hparams` 会创建一个新的 target Pod，如果没有模型缓存，完整矩阵可能会触发多次下载。真实测试建议提前把模型放到 target 镜像、节点本地模型目录，或后续单独给 target Pod 增加模型/cache 挂载。

注意：`MODEL_METADATA_HOST_PATH` 只解决资源规划读取 metadata，不等于 target vLLM 的模型权重缓存。

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

- `configs/model_metadata.example/`
  - 本地示例 metadata 目录，只用于演示资源规划输入。

关键 env：

```env
NAMESPACE=bench
MASTER_IMAGE=vllm-bench-platform/master:local
TARGET_VLLM_IMAGE=vllm/vllm-openai:v0.8.5
TARGET_RESOURCE_NAME=nvidia.com/gpu
TARGET_GPU_MEMORY_GB=8
MODEL_METADATA_HOST_PATH=configs/model_metadata.example
MODEL_PATH=Qwen/Qwen2.5-0.5B-Instruct
MODEL_NAME=Qwen2.5-0.5B-Instruct
SERVED_MODEL_NAME=Qwen2.5-0.5B-Instruct
DTYPE=float16
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
4. Master 读取 `/model-metadata`，示例 metadata 估算结果为 1 张 GPU。
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

如果资源规划算出需要 2 张或更多 GPU，而集群只有 1 张卡，target Pod 会因为资源不足 Pending，Master 会记录失败 case 和 events。

## 后续 TODO（已完成）

详细执行计划见 `docs/implementation-plan.md`。

## 额外 TODO
模型挂载，防止每次都去Hugging Face重新下载。
