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
