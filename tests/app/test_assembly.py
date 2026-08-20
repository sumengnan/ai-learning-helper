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
    assert h.registry.get("search_knowledge") is not None    # api_key 有 → 记忆注册
    assert h.registry.get("browse") is None                # 未启用
    assert h.registry.get("run_python") is None             # 未启用沙箱
    assert h.registry.get("dispatch") is None               # 未启用派发


def test_browser_needs_sandbox():
    """浏览器统一走沙箱：只 enable_browser 但没配沙箱 → browse 不注册（宿主不再本地抓）。"""
    h = build_harness(_cfg(enable_browser=True, enable_sandbox=False))
    assert h.registry.get("browse") is None


def test_browser_gated_on():
    # 有沙箱才注册 browse（浏览器在沙箱容器内跑）
    h = build_harness(_cfg(enable_browser=True, enable_sandbox=True,
                           sandbox_backend="docker", sandbox_docker_host="tcp://stub:2376"))
    assert h.registry.get("browse") is not None


def test_lang_images_expose_multilang_code_tools():
    # run_node/run_java 按 sandbox_lang_images 是否含该语言镜像注册（数据驱动，前缀匹配）；
    # 只配 java8 → 暴露 run_java（java 前缀），但无 node → 不暴露 run_node。
    # SandboxManager/SandboxProxy 惰性建容器，构建期不连 daemon。
    h = build_harness(_cfg(enable_sandbox=True, sandbox_backend="docker",
                           sandbox_docker_host="tcp://stub:2376",
                           sandbox_lang_images={"java8": "eclipse-temurin:8-jdk"}))
    assert h.registry.get("run_python") is not None
    assert h.registry.get("run_java") is not None      # java8 命中 java 前缀
    assert h.registry.get("run_node") is None          # 无 node 镜像 → 不暴露


def test_no_lang_images_no_multilang_code_tools():
    # 未配 sandbox_lang_images → 只有 run_python，无 run_java/run_node。
    # 显式清空：config 默认已预置多语言镜像映射，这里要测「操作者未配置」的场景。
    h = build_harness(_cfg(enable_sandbox=True, sandbox_backend="docker",
                           sandbox_docker_host="tcp://stub:2376",
                           sandbox_lang_images={}))
    assert h.registry.get("run_python") is not None
    assert h.registry.get("run_java") is None
    assert h.registry.get("run_node") is None


def _agents_dir(tmp_path, fname, content):
    (tmp_path / fname).write_text(content, encoding="utf-8")
    return str(tmp_path)


def test_dispatch_gated_on(tmp_path):
    # api_key 有 → search_knowledge 在池；http_request 恒在 → researcher 有可用工具
    d = _agents_dir(tmp_path, "researcher.yaml",
                    "name: researcher\ndescription: 检索\nsystem_prompt: 你是研究员\n"
                    "tool_names: [search_knowledge, http_request]\n")
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


def test_prompt_has_search_guidance():
    """联网检索指引必须进系统提示：否则模型会把用户原话整句当 query 搜一次就下笔。

    这条只能靠提示词——搜索工具是 MCP 接入的第三方工具，描述改不了；而每步检索校验
    只判空命中，一条宽泛 query 照样返回若干条非空结果，必然放行。
    """
    sp = build_harness(_cfg()).system_prompt
    assert "未来 N 年" in sp          # 相对时间必须换算成绝对年份再进 query
    assert "正交的子查询" in sp        # 宽泛问题要拆
    assert "不要为拆而拆" in sp        # 但简单事实查询不该被拖成多次检索


# ---- 快速模型档的接线 ----

def test_memory_writer_extract_uses_fast_completer_reconcile_stays_main():
    """提炼走快速档、调和留主模型。

    这是「快速模型」改造在 assembly 侧的全部内容：单测 MemoryWriter 本身管不到接线，
    把 extract_complete= 那行删掉，那些单测照样全绿。

    调和之所以必须留主模型：它判 REPLACE 就会 set_superseded 永久作废旧记忆，
    判错不是省钱是毁数据。
    """
    h = build_harness(_cfg(memory_write_extract=True, enable_browser=False,
                           enable_sandbox=False))
    w = h.memory_writer
    assert w is not None, "memory_write_extract=true 时应装配 MemoryWriter"
    assert w._extract_complete is not w._complete, "提炼与调和须是两个不同的 completer"


def test_memory_writer_not_built_when_extract_disabled():
    h = build_harness(_cfg(memory_write_extract=False, enable_browser=False,
                           enable_sandbox=False))
    assert h.memory_writer is None


def test_maintainer_and_hyde_wired_to_fast_completer(monkeypatch):
    """整合蒸馏与查询期召回增强都须接快速档 —— 二者都是机械活。

    接线测试不可省：把 build_fast_completer 换回 build_completer，别处没有任何测试会红。
    这里把 build_fast_completer 换成哨兵，看它到底有没有被传进去（assembly 是在函数体内
    import 的，故打桩 app.completion 上的名字即可生效）。
    """
    import app.completion as C
    sentinel = object()
    monkeypatch.setattr(C, "build_fast_completer", lambda client, cfg: sentinel)
    h = build_harness(_cfg(enable_browser=False, enable_sandbox=False))
    # 两处都被 with_role 包了一层固定采样温度（整合 0.2 / 查询改写 0.5），故穿透包装再比身份
    from app.completion import unwrap_completer
    assert unwrap_completer(h.memory_maintainer._complete) is sentinel, "整合蒸馏应接快速档"
    assert unwrap_completer(h.memory._retriever._complete) is sentinel, "HyDE/多查询改写应接快速档"


@pytest.mark.asyncio
async def test_fast_tier_sites_all_declare_thinking_off():
    """快速档的四个站点（整合/HyDE/导入/提炼）发出的 extra_body 必须显式关思考。

    此前它们什么都不发，由模型服务端默认决定（Qwen3 系默认开思考）——同一份代码换个
    供应商行为就变，且无从在代码里看出会发生什么。
    """
    from harness.llm.base import StreamChunk
    from harness.llm.openai_compat import get_extra_body_override
    from app.completion import build_fast_completer, build_completer

    seen = {}

    class _Probe:
        def __init__(self, tag):
            self._tag = tag

        async def stream(self, messages, tools):
            seen[self._tag] = get_extra_body_override().get("enable_thinking", "未声明")
            yield StreamChunk(type="text", text="x")
            yield StreamChunk(type="done")

    cfg = _cfg()
    await build_fast_completer(_Probe("fast"), cfg)("s", "u")
    await build_completer(_Probe("bare"), cfg.model)("s", "u")
    assert seen["fast"] is False, "快速档须显式关思考"
    assert seen["bare"] == "未声明", "主模型裸调仍不声明（对照组：证明上面那条不是白测）"


def test_terminal_review_honors_judge_model():
    """编排器的终局校验（用户看到的「结果校验」）必须吃 judge 配置。

    回归：review 曾写死主模型，而 judge 只接在交付门 AnswerVerifier 上——编排器成为
    唯一主流程后那条分支永不进入，HARNESS_JUDGE_MODEL 于是对结果校验完全不起作用，
    且不报错、不留痕。这类「配置静默失效」没有测试就发现不了。
    """
    h = build_harness(_cfg(judge_model="judge-x", judge_base_url="https://judge.example/v1",
                           judge_api_key="jk"))
    # completer 是闭包，从绑定的 client/模型上取证：judge 配了独立端点则该 client 不是主 client
    critic = h.orchestrator._critic
    assert "judge-x" in _closure_values(critic._complete), "终局 review 没走 judge 模型"
    # validate 现已上调到 judge、与 review 同一个 completer（此前走快速档、故意用 is not/不含
    # judge-x 反证；现改成同走 judge，两条断言随之反转）。
    assert critic._complete is critic._validate, "单步 validate 现应与 review 同走 judge"
    assert "judge-x" in _closure_values(critic._validate), "单步 validate 没走 judge 模型"


def _closure_values(fn, depth=6) -> set:
    """收集嵌套闭包里绑定的所有字符串值。

    completer 是层层包裹的闭包（build_judge_completer → _with_thinking → build_completer），
    模型名藏在最内层，只看一层拿不到。
    """
    import inspect
    out, seen = set(), set()
    stack = [(fn, 0)]
    while stack:
        f, d = stack.pop()
        if d > depth or id(f) in seen:
            continue
        seen.add(id(f))
        try:
            vals = inspect.getclosurevars(f).nonlocals.values()
        except TypeError:
            continue
        for v in vals:
            if isinstance(v, str):
                out.add(v)
            elif callable(v):
                stack.append((v, d + 1))
            else:
                out.add(getattr(v, "model", None) or getattr(v, "_model", None) or "")
    return out


def test_rerank_multi_query_flag_reaches_the_retriever():
    """配置里的精排均分开关必须真的传到 Retriever。

    RetrievalConfig 的字段是一条条手抄过去的，漏抄一个不会报错、只会静默失效——
    开关拨了没反应，排查起来先怀疑的是模型而不是接线。
    """
    h = build_harness(_cfg(retrieval_rerank_multi_query=True))
    assert h.memory._retriever._config.rerank_multi_query is True


def test_rerank_multi_query_defaults_off():
    """默认关：每条改写多一次精排调用，得显式开。"""
    h = build_harness(_cfg())
    assert h.memory._retriever._config.rerank_multi_query is False
