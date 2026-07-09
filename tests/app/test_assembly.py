import pytest

from app.config import AppConfig
from app.assembly import build_harness, Harness

_DL_DIR = ""


@pytest.fixture(scope="session", autouse=True)
def _dl_dir(tmp_path_factory):
    # 由 pytest 托管的临时目录（会自动清理），供 build_harness 的 DownloadStore 落盘，
    # 不在 cwd、也不像 tempfile.mkdtemp 那样泄漏残留目录。
    global _DL_DIR
    _DL_DIR = str(tmp_path_factory.mktemp("dl"))


def _cfg(**kw):
    return AppConfig(api_key="k", persistence_db_path=":memory:", memory_db_path=":memory:",
                     downloads_dir=_DL_DIR, app_db_path=":memory:", _env_file=None, **kw)


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


def test_skills_gated_off():
    h = build_harness(_cfg(enable_skills=False))
    assert h.registry.get("load_skill") is None
    assert h.skill_registry is None


def test_skills_gated_on_registers_tools(tmp_path):
    d = tmp_path / "etl"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: etl\ndescription: 表格清洗\n---\n正文", encoding="utf-8")
    h = build_harness(_cfg(enable_skills=True, skills_dir=str(tmp_path)))
    assert h.registry.get("load_skill") is not None
    assert h.registry.get("unload_skill") is not None
    assert h.registry.get("read_skill_resource") is not None
    assert h.skill_registry is not None
    assert h.skill_registry.has("etl")


def test_skills_gated_on_empty_dir_registers_nothing(tmp_path):
    h = build_harness(_cfg(enable_skills=True, skills_dir=str(tmp_path / "empty")))
    assert h.registry.get("load_skill") is None
    assert h.skill_registry is None
