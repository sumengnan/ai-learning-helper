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
