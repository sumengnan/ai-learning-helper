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


def test_lang_images_expose_multilang_code_tools():
    # 配了 sandbox_lang_images（语言/版本子沙箱）即注册 run_node/run_java；
    # SandboxManager/SandboxProxy 惰性建容器，构建期不连 daemon。
    h = build_harness(_cfg(enable_sandbox=True, sandbox_backend="docker",
                           sandbox_docker_host="tcp://stub:2376",
                           sandbox_lang_images={"java8": "eclipse-temurin:8-jdk"}))
    assert h.registry.get("run_python") is not None
    assert h.registry.get("run_java") is not None
    assert h.registry.get("run_node") is not None


def test_no_lang_images_no_multilang_code_tools():
    # 未配 sandbox_lang_images 且未配路由 → 只有 run_python，无 run_java/run_node
    h = build_harness(_cfg(enable_sandbox=True, sandbox_backend="docker",
                           sandbox_docker_host="tcp://stub:2376"))
    assert h.registry.get("run_python") is not None
    assert h.registry.get("run_java") is None
    assert h.registry.get("run_node") is None


def _agents_dir(tmp_path, fname, content):
    (tmp_path / fname).write_text(content, encoding="utf-8")
    return str(tmp_path)


def test_dispatch_gated_on(tmp_path):
    # api_key 有 → search_memory 在池；http_request 恒在 → researcher 有可用工具
    d = _agents_dir(tmp_path, "researcher.yaml",
                    "name: researcher\ndescription: 检索\nsystem_prompt: 你是研究员\n"
                    "tool_names: [search_memory, http_request]\n")
    h = build_harness(_cfg(enable_dispatch=True, agents_dir=d))
    assert h.registry.get("dispatch") is not None


def test_dispatch_skips_agent_with_no_available_tools(tmp_path):
    # coder 只引用沙箱工具，但未启用沙箱 → 工具全不可用 → 跳过该 agent → 无 agent → 不注册 dispatch
    d = _agents_dir(tmp_path, "coder.yaml",
                    "name: coder\ndescription: 写码\nsystem_prompt: 你是程序员\n"
                    "tool_names: [run_python, run_shell]\n")
    h = build_harness(_cfg(enable_dispatch=True, agents_dir=d))
    assert h.registry.get("dispatch") is None


def test_dispatch_empty_agents_dir_no_dispatch(tmp_path):
    h = build_harness(_cfg(enable_dispatch=True, agents_dir=str(tmp_path / "empty")))
    assert h.registry.get("dispatch") is None


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


def test_update_plan_registered_and_prompt_has_guidance():
    h = build_harness(_cfg())
    assert h.registry.get("update_plan") is not None
    assert "update_plan" in h.system_prompt
