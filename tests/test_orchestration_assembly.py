from app.assembly import build_harness
from app.config import AppConfig


def test_orchestrator_absent_by_default(monkeypatch):
    # 注：build_harness 无论是否启用编排器都会立即构造 AsyncOpenAI 客户端，空 api_key
    # 且环境无 OPENAI_API_KEY 回退时会在构造期直接报错——与本测试意图（编排器默认不装配）
    # 无关，故这里用非空占位 key（与 tests/app/test_assembly.py 的 api_key="k" 惯例一致）。
    monkeypatch.setenv("HARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("HARNESS_ENABLE_ORCHESTRATOR", "false")
    h = build_harness(AppConfig())
    assert getattr(h, "orchestrator", None) is None


def test_orchestrator_built_when_enabled(monkeypatch):
    monkeypatch.setenv("HARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("HARNESS_ENABLE_ORCHESTRATOR", "true")
    h = build_harness(AppConfig())
    from app.orchestration.orchestrator import Orchestrator
    assert isinstance(h.orchestrator, Orchestrator)


def test_executor_registry_excludes_update_plan(monkeypatch):
    """执行子步不该拿到 update_plan：否则子步一调它就发 scope=plan 覆盖编排器的总计划（bug）。"""
    monkeypatch.setenv("HARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("HARNESS_ENABLE_ORCHESTRATOR", "true")
    o = build_harness(AppConfig()).orchestrator
    assert o._executor._registry.get("update_plan") is None      # 执行步无 update_plan
    assert o._registry.get("update_plan") is not None            # 主 registry 仍有（simple 直答走 ReAct 可用）


def test_executor_registry_sees_tools_registered_after_build(monkeypatch):
    """MCP 工具是 startup（build 之后）才注册进 reg 的；执行子步的 registry 是活视图，应能看到。"""
    from harness.tools.base import Tool
    from pydantic import BaseModel
    monkeypatch.setenv("HARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("HARNESS_ENABLE_ORCHESTRATOR", "true")
    h = build_harness(AppConfig())

    class _Fake(Tool):
        name = "mcp__websearch__bailian_web_search"
        description = "联网搜索"
        class Params(BaseModel):
            pass
        async def run(self, params):
            return "ok"
    h.registry.register(_Fake())   # 模拟 startup 注册 MCP 工具

    exec_reg = h.orchestrator._executor._registry
    assert exec_reg.get("mcp__websearch__bailian_web_search") is not None  # 活视图能看到晚注册的工具
    assert any(t.name == "mcp__websearch__bailian_web_search" for t in exec_reg.tools())


def test_orchestrator_speed_wiring(monkeypatch):
    """A 组提速接线生效：执行子步默认关思考、Critic.validate 与 review 用不同 completer、
    预算工厂已挂（每 run 独立封顶）。"""
    monkeypatch.setenv("HARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("HARNESS_ENABLE_ORCHESTRATOR", "true")
    o = build_harness(AppConfig()).orchestrator
    assert o._executor._disable_thinking is True                 # 子步关思考
    assert o._critic._validate is not o._critic._complete        # validate 走独立(快速档)completer
    assert o._budget_factory is not None                         # 预算工厂已挂
    b = o._budget_factory()
    from harness.reliability.budget import BudgetTracker
    assert isinstance(b, BudgetTracker) and o._budget_factory() is not b  # 每次新建独立实例
