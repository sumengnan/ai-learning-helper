from app.config import AppConfig
from app.assembly import build_harness, Harness


def _cfg(**kw):
    return AppConfig(api_key="k", persistence_db_path=":memory:", memory_db_path=":memory:", **kw)


def test_core_tools_registered_heavy_gated_off():
    h = build_harness(_cfg(enable_browser=False, enable_sandbox=False))
    assert isinstance(h, Harness)
    assert h.registry.get("calculator") is not None
    assert h.registry.get("http_request") is not None
    assert h.registry.get("search_memory") is not None    # api_key 有 → 记忆注册
    assert h.registry.get("browse") is None                # 未启用
    assert h.registry.get("run_python") is None             # 未启用沙箱
    assert h.registry.get("dispatch") is None               # 未启用派发


def test_browser_gated_on():
    h = build_harness(_cfg(enable_browser=True))
    assert h.registry.get("browse") is not None


def test_dispatch_gated_on():
    h = build_harness(_cfg(enable_dispatch=True, enable_browser=True))
    assert h.registry.get("dispatch") is not None
