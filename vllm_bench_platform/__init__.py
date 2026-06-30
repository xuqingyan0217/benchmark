"""国产卡 vLLM 自动化压测平台。

包级职责说明：
- 这里是平台 Python 代码的根包，承载 backend 和 master-controller。
- 根包不放业务逻辑，避免不同运行角色在 import 时产生隐式副作用。
- 共享 schema 放在 `schemas.py`，由 backend 和 master 共同依赖。
- backend 负责接收用户提交、生成 Kubernetes MVP 资源，并返回 run 身份。
- master-controller 负责在集群内按 serve x bench 矩阵编排 target，并直接执行 benchmark case。
- OpenSpec 是本项目的需求事实来源；新增行为必须先进入对应 change。
- 本包入口不导出快捷 API，是为了防止调用方绕过已经定义好的模块边界。
- 后续若要增加 CLI 或 Web API，应通过 backend 子包暴露，而不是污染根包。
"""
