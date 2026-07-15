"""把 .env 补进 os.environ：mcp_servers.json 的 ${VAR} 只认 os.environ，
而 pydantic-settings 读 .env 只填配置对象、不写环境——两者接不上会导致静默 401。"""
import os

from app.config import load_env_file


def _write(tmp_path, text):
    p = tmp_path / ".env"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_loads_plain_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("FOO_K", raising=False)
    injected = load_env_file(_write(tmp_path, "FOO_K=v1\n"))
    assert injected == ["FOO_K"] and os.environ["FOO_K"] == "v1"


def test_real_env_wins_and_is_not_overwritten(tmp_path, monkeypatch):
    # export / docker -e / k8s env 说了算，.env 只是兜底
    monkeypatch.setenv("FOO_K", "来自真环境")
    injected = load_env_file(_write(tmp_path, "FOO_K=来自dotenv\n"))
    assert injected == [] and os.environ["FOO_K"] == "来自真环境"


def test_skips_comments_blanks_and_malformed(tmp_path, monkeypatch):
    for k in ("A", "B"):
        monkeypatch.delenv(k, raising=False)
    injected = load_env_file(_write(tmp_path, "\n# 注释\nA=1\n没有等号\n\n  B=2  \n"))
    assert injected == ["A", "B"] and os.environ["A"] == "1" and os.environ["B"] == "2"


def test_strips_export_prefix_and_quotes(tmp_path, monkeypatch):
    for k in ("E1", "Q1", "Q2"):
        monkeypatch.delenv(k, raising=False)
    load_env_file(_write(tmp_path, 'export E1=x\nQ1="dq"\nQ2=\'sq\'\n'))
    assert os.environ["E1"] == "x" and os.environ["Q1"] == "dq" and os.environ["Q2"] == "sq"


def test_value_containing_equals_is_kept_whole(tmp_path, monkeypatch):
    monkeypatch.delenv("URLK", raising=False)
    load_env_file(_write(tmp_path, "URLK=https://x.com/?a=1&b=2\n"))
    assert os.environ["URLK"] == "https://x.com/?a=1&b=2"


def test_missing_file_is_noop(tmp_path):
    assert load_env_file(str(tmp_path / "不存在.env")) == []


def test_expandvars_sees_loaded_key(tmp_path, monkeypatch):
    # 这才是这个函数存在的理由：让 mcp 清单里的 ${VAR} 能解析
    monkeypatch.delenv("MY_MCP_KEY", raising=False)
    load_env_file(_write(tmp_path, "MY_MCP_KEY=sk-real\n"))
    assert os.path.expandvars("Bearer ${MY_MCP_KEY}") == "Bearer sk-real"


def test_create_app_does_not_touch_environ_when_config_injected(tmp_path, monkeypatch):
    """调用方自带 config（测试/嵌入式）→ 不加载 .env，避免开发机 .env 污染测试。

    生产路径（python -m app → uvicorn factory）不传 config，那时才加载。
    """
    import app.config as appcfg
    import app.main as main
    from app.config import AppConfig
    from app.conversations import ConversationStore
    from app.documents import DocumentStore

    calls = []
    monkeypatch.setattr(main, "load_env_file", lambda p: calls.append(p) or [])
    monkeypatch.setattr(main, "setup_telemetry", lambda c: None)
    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None)

    class _Cli:
        async def stream(self, messages, schemas):
            yield None

    from harness.persistence.checkpoint import CheckpointStore
    from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
    from harness.tools.base import ToolRegistry
    traj = TrajectoryStore(":memory:")
    from app.assembly import Harness
    h = Harness(client=_Cli(), registry=ToolRegistry(),
                checkpoint_store=CheckpointStore(":memory:"),
                trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="s")
    main.create_app(config=cfg, harness=h, store=ConversationStore(":memory:"),
                    doc_store=DocumentStore(":memory:"))
    assert calls == []        # 传了 config → 一次都没加载
    assert appcfg.load_env_file is not None
