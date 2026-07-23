from app.assembly import build_harness
from app.config import AppConfig


def test_orchestrator_always_built(monkeypatch):
    """编排器已是唯一主流程，无开关 → build_harness 恒构建它。
    注：build_harness 会立即构造 AsyncOpenAI 客户端，空 api_key 且环境无 OPENAI_API_KEY 回退时
    会在构造期报错，故用非空占位 key（与 tests/app/test_assembly.py 的 api_key="k" 惯例一致）。"""
    monkeypatch.setenv("HARNESS_API_KEY", "sk-test")
    from app.orchestration.orchestrator import Orchestrator
    assert isinstance(build_harness(AppConfig()).orchestrator, Orchestrator)


def test_executor_registry_excludes_update_plan(monkeypatch):
    """执行子步不该拿到 update_plan：否则子步一调它就发 scope=plan 覆盖编排器的总计划（bug）。"""
    monkeypatch.setenv("HARNESS_API_KEY", "sk-test")
    o = build_harness(AppConfig()).orchestrator
    assert o._executor._registry.get("update_plan") is None      # 执行步无 update_plan
    assert o._registry.get("update_plan") is not None            # 主 registry 仍有（simple 直答走 ReAct 可用）


def test_executor_registry_sees_tools_registered_after_build(monkeypatch):
    """MCP 工具是 startup（build 之后）才注册进 reg 的；执行子步的 registry 是活视图，应能看到。"""
    from harness.tools.base import Tool
    from pydantic import BaseModel
    monkeypatch.setenv("HARNESS_API_KEY", "sk-test")
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
    """A 组提速接线生效：执行子步默认关思考、预算工厂已挂（每 run 独立封顶）。

    注：validate 曾走独立快速档，现已上调到 judge、与 review 同一个 completer
    （见 test_single_step_validate_wired_to_judge_completer）——故此处它俩相等，不再是独立。
    """
    monkeypatch.setenv("HARNESS_API_KEY", "sk-test")
    o = build_harness(AppConfig()).orchestrator
    assert o._executor._disable_thinking is True                 # 子步关思考
    assert o._critic._validate is o._critic._complete            # validate 与 review 同走 judge 档
    # 简单直答走快速档 client/model（与执行子步同源）；未配 fast_model 时回退主 client/主模型
    assert o._fast_client is o._executor._client
    assert o._fast_model == o._executor._model
    assert o._fast_max_prompt_tokens == 0                        # 快速上下文上限默认关（0）


def test_orchestrator_fast_prompt_cap_wired(monkeypatch):
    """配了 context_max_prompt_tokens_fast → 传进编排器，供简单直答按快速模型口径重裁。"""
    monkeypatch.setenv("HARNESS_API_KEY", "sk-test")
    monkeypatch.setenv("HARNESS_CONTEXT_MAX_PROMPT_TOKENS_FAST", "32000")
    o = build_harness(AppConfig()).orchestrator
    assert o._fast_max_prompt_tokens == 32000
    assert o._budget_factory is not None                         # 预算工厂已挂
    b = o._budget_factory()
    from harness.reliability.budget import BudgetTracker
    assert isinstance(b, BudgetTracker) and o._budget_factory() is not b  # 每次新建独立实例


def test_single_step_validate_wired_to_judge_completer(monkeypatch):
    """单步质检 validate 走 judge 档（不再走快速档）。

    validate 现在能判 impossible——终结该步且抑制重规划，一次误判代价放大到整条任务分支，
    故与终局 review 同级由裁判模型判。接线是这次改动的全部内容：只单测 Critic/completer
    本身不够，把 validate 打回快速档它们照样全绿，必须在装配层锁住。
    """
    monkeypatch.setenv("HARNESS_API_KEY", "sk-test")
    import app.completion as C
    from app.orchestration import critic as critic_mod
    judge_sentinel = object()
    fast_sentinel = object()
    monkeypatch.setattr(C, "build_judge_completer", lambda client, cfg: judge_sentinel)
    monkeypatch.setattr(C, "build_fast_completer", lambda client, cfg: fast_sentinel)

    captured = {}
    real_critic = critic_mod.Critic
    def _spy(complete, *, validate_complete=None, **kw):
        captured["review"] = complete
        captured["validate"] = validate_complete or complete   # Critic 内部同款回退
        return real_critic(complete, validate_complete=validate_complete, **kw)
    monkeypatch.setattr(critic_mod, "Critic", _spy)

    build_harness(AppConfig())
    from app.completion import unwrap_completer
    # 外面包了一层 with_role（critic 恒 0 温），穿透后再比身份
    assert unwrap_completer(captured["validate"]) is judge_sentinel, "单步 validate 必须走 judge 档"
    assert unwrap_completer(captured["review"]) is judge_sentinel   # review 本就是 judge
    assert captured["validate"] is not fast_sentinel  # 明确：不再是快速档
