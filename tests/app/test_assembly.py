import tempfile
from app.config import AppConfig
from app.assembly import build_harness, Harness

_DL_DIR = tempfile.mkdtemp()


def _cfg(**kw):
    return AppConfig(api_key="k", persistence_db_path=":memory:", memory_db_path=":memory:",
                     downloads_dir=_DL_DIR, downloads_db_path=":memory:", **kw)


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


def test_build_harness_exposes_memory():
    h = build_harness(_cfg())        # _cfg 已设 api_key + :memory: dbs
    assert h.memory is not None
    assert h.memory_store is not None


def test_build_harness_registers_save_download():
    h = build_harness(_cfg())
    assert h.registry.get("save_download") is not None
    assert h.download_store is not None
