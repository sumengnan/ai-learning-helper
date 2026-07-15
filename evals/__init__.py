# evals/__init__.py
"""离线评测系统。开发期工具，不进生产镜像（Dockerfile 是显式 COPY 白名单）。

依赖方向严格单向：evals → app → harness。内核不知道 evals 存在。
分两层：
  - 快层（mock）：组件级准确率回归，零网络、秒级、确定性 → 由 tests/evals/ 当 CI 门禁跑
  - 慢层（real）：端到端 agent 质量，真实 API + LLM judge → 由 evals/cli.py 手动跑
"""
