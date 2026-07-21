"""单步重跑清理产物的端到端：文件真的从磁盘和 downloads 表消失。

分层测试盖不到的那一段：orchestrator 的用例只断言"回调被调用"，side_effects 的用例
只断言 fake 收到了删除请求。真正把两头接起来的是 chat.py 里那段装配——
SideEffectPurger 的构造、`harness.download_store` 这个属性名、_purge_step_fx 闭包。
属性名写错、store 没接上，上面两层测试全绿而线上一个文件都删不掉。
"""
import os
import sqlite3

import pytest

from app.downloads import DownloadStore
from app.side_effects import SideEffectPurger, ids_from_tool


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    orig = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: orig(*a, **{**k, "check_same_thread": False}))


def _store(tmp_path) -> DownloadStore:
    return DownloadStore(files_dir=str(tmp_path / "files"), db_path=":memory:")


def test_purge_removes_file_from_disk_and_table(tmp_path):
    store = _store(tmp_path)
    rec = store.create("u1", "笔记.md", "# 草稿".encode("utf-8"), "text/markdown")
    assert os.path.exists(store.path(rec["id"]))

    done = SideEffectPurger(download_store=store).purge(
        "u1", {"download": [rec["id"]], "knowledge": [], "questions": []})

    assert done["download"] == [rec["id"]]
    assert not os.path.exists(store.path(rec["id"])), "磁盘文件应已删除"
    assert store.get("u1", rec["id"]) is None, "downloads 表记录应已删除"


def test_purge_wont_delete_another_users_file(tmp_path):
    """产物 id 是从工具结果文本扒出来的，绑定的 user_id 必须挡住越权删除。"""
    store = _store(tmp_path)
    rec = store.create("u1", "笔记.md", b"x", "text/markdown")
    done = SideEffectPurger(download_store=store).purge(
        "u2", {"download": [rec["id"]], "knowledge": [], "questions": []})
    assert done["download"] == [], "别人的文件不该被算作已清理"
    assert os.path.exists(store.path(rec["id"])), "别人的文件不该被删"


def test_marker_from_real_save_download_round_trips(tmp_path):
    """真实 save_download 的 marker 形状 → ids_from_tool 能扒出来 → purger 能删掉。

    这条链上任何一环改了格式（marker 模板、正则），这个测试就该红。
    """
    store = _store(tmp_path)
    rec = store.create("u1", "报告.md", "内容".encode("utf-8"), "text/markdown")
    # 与 app/tools/save_download.py 的 marker 拼法一致
    result_text = f"已保存到下载区：报告.md（6 字节）。〔下载ID:{rec['id']}〕"

    fx = ids_from_tool("save_download", result_text, is_error=False)
    assert fx["download"] == [rec["id"]]

    SideEffectPurger(download_store=store).purge("u1", fx)
    assert not os.path.exists(store.path(rec["id"]))


def test_harness_exposes_download_store_under_expected_name():
    """chat.py 用 getattr(harness, "download_store") 取 store —— 属性名写错会静默不清理。"""
    from app.assembly import Harness
    assert "download_store" in Harness.__dataclass_fields__ or hasattr(
        Harness, "download_store"), "属性名变了，chat.py 的 getattr 会静默拿到 None"


def test_chat_purge_callback_returns_grouped_dict_not_just_ids():
    """chat 注入编排器的清理回调必须回传**按类分组的 dict**。

    编排器要据此把对应工具名从该步 done_effects 里摘掉、让模型重做。只回下载 id 列表
    的话它认不出删的是哪一类，接缝静默失效：文件删了、模型仍被告知"已存过"、不再保存，
    用户手里一个文件都没有。而两层各自的测试都会绿——故在这里钉住形状。
    """
    import inspect

    from app.api import chat as chat_mod
    src = inspect.getsource(chat_mod.make_chat_router)
    assert "return done" in src, "回调应回传 purge() 的完整结果"
    assert 'return done["download"]' not in src, (
        "回传仅下载 id 会让「删了就让模型重做」的接缝静默失效")


def test_end_state_is_exactly_one_file_after_a_failed_attempt(tmp_path):
    """两套机制协作的终局：重跑后用户手里**恰好一份**文件——不是零份也不是两份。

    零份 = 清理了但模型被告知"已存过"、不再保存（合并时的必然 bug，接缝正是为它而加）。
    两份 = 模型重存了但旧的没清掉（本特性要解决的原始问题）。
    这条直接盯终局状态，不关心中间用了哪套机制，故任何一侧退化它都会红。
    """
    from app.side_effects import SideEffectPurger, ids_from_tool, tools_to_redo

    store = _store(tmp_path)
    done_effects: list[str] = []          # 模拟编排器的 step_effects[step.id]

    # —— 第一次尝试：模型存了文件 ——
    rec1 = store.create("u1", "报告.md", "初稿".encode("utf-8"), "text/markdown")
    done_effects.append("save_download")
    fx = ids_from_tool("save_download", f"已保存〔下载ID:{rec1['id']}〕", is_error=False)

    # —— 校验不过：清理产物，并按实际删掉的摘掉工具名 ——
    purged = SideEffectPurger(download_store=store).purge("u1", fx)
    done_effects = [t for t in done_effects if t not in tools_to_redo(purged)]

    assert not os.path.exists(store.path(rec1["id"])), "作废那版的文件应已删除"
    assert "save_download" not in done_effects, "删了就得让模型重做，否则用户一份都拿不到"

    # —— 重跑：模型据此重新保存 ——
    rec2 = store.create("u1", "报告.md", "改进稿".encode("utf-8"), "text/markdown")

    remaining = [r for r in store.list("u1")]
    assert len(remaining) == 1, f"应恰好剩一份，实际 {len(remaining)} 份"
    assert remaining[0]["id"] == rec2["id"], "留下的应是重跑后的新版"


def test_purge_must_not_delete_a_reused_preexisting_file(tmp_path):
    """内容去重命中旧记录时，清理不能把用户早先存的文件删掉。

    create() 按 (user, sha256) 在**该用户全部历史**里去重，命中就返回旧记录的 id、不新建。
    于是：某步存了一份内容，恰好与用户上周存过的一模一样 → 拿到的是上周那条记录的 id →
    本步校验不过 → 按 id 清理 → **上周那个文件被删了**。产物 id 并不等于"本次尝试新建的"。
    """
    store = _store(tmp_path)
    old = store.create("u1", "上周的笔记.md", "同样的内容".encode("utf-8"), "text/markdown")

    # 本次尝试存了一模一样的内容 → 去重命中，拿到的是上周那条的 id
    again = store.create("u1", "本次的笔记.md", "同样的内容".encode("utf-8"), "text/markdown")
    assert again["id"] == old["id"], "前提：去重确实命中了旧记录"

    # 工具只登记**新建**的 id；这次是复用，故集合为空
    created: set = set()
    assert again.get("reused") is True
    SideEffectPurger(download_store=store, created_downloads=created).purge(
        "u1", {"download": [again["id"]], "knowledge": [], "questions": []})

    assert os.path.exists(store.path(old["id"])), "用户早先存的文件不该被这次清理删掉"


def test_newly_created_file_is_still_purged(tmp_path):
    """别把上一条修过头：本轮真新建的文件照删不误。"""
    store = _store(tmp_path)
    rec = store.create("u1", "本轮的.md", "新内容".encode("utf-8"), "text/markdown")
    assert rec.get("reused") is False
    created = {rec["id"]}          # SaveDownloadTool 会这样登记
    done = SideEffectPurger(download_store=store, created_downloads=created).purge(
        "u1", {"download": [rec["id"]], "knowledge": [], "questions": []})
    assert done["download"] == [rec["id"]]
    assert not os.path.exists(store.path(rec["id"]))


def test_save_download_tool_registers_only_new_ids(tmp_path):
    """接线校验：工具把新建的登记进集合、复用的不登记。这两头对不上，上面两条就都成了摆设。"""
    import asyncio

    from app.tools.save_download import SaveDownloadTool

    store = _store(tmp_path)
    created: set = set()
    tool = SaveDownloadTool(store, 10 * 1024 * 1024, "u1", created_ids=created)

    async def _save(name, content):
        return await tool.run(tool.Params(filename=name, content=content))

    asyncio.run(_save("甲.md", "同一份内容"))
    assert len(created) == 1, "新建的应登记"
    asyncio.run(_save("乙.md", "同一份内容"))      # 内容相同 → 去重命中，复用旧记录
    assert len(created) == 1, "复用的不该登记，否则清理会删掉早先的文件"
