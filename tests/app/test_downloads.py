import os
from app.downloads import DownloadStore


def _store(tmp_path):
    return DownloadStore(str(tmp_path / "files"), ":memory:")


def test_create_writes_file_and_registers(tmp_path):
    s = _store(tmp_path)
    rec = s.create("u1", "笔记.md", b"# hi", "text/markdown")
    assert rec["filename"] == "笔记.md" and rec["size"] == 4
    assert rec["content_type"] == "text/markdown"
    assert os.path.exists(s.path(rec["id"]))                 # 落盘
    with open(s.path(rec["id"]), "rb") as f:
        assert f.read() == b"# hi"


def test_list_newest_first(tmp_path):
    s = _store(tmp_path)
    a = s.create("u1", "a.txt", b"a", "text/plain")["id"]
    b = s.create("u1", "b.txt", b"b", "text/plain")["id"]
    assert [r["id"] for r in s.list("u1")] == [b, a]         # 倒序


def test_get_includes_path_and_none_missing(tmp_path):
    s = _store(tmp_path)
    rid = s.create("u1", "x.txt", b"x", "text/plain")["id"]
    got = s.get("u1", rid)
    assert got["path"] == s.path(rid) and got["filename"] == "x.txt"
    assert s.get("u1", "nope") is None


def test_delete_removes_file_and_row(tmp_path):
    s = _store(tmp_path)
    rid = s.create("u1", "x.txt", b"x", "text/plain")["id"]
    p = s.path(rid)
    assert s.delete("u1", rid) is True
    assert not os.path.exists(p)                             # 文件删了
    assert s.get("u1", rid) is None                          # 登记删了
    assert s.delete("u1", rid) is False                      # 再删不存在


def test_isolation_between_users(tmp_path):
    s = _store(tmp_path)
    rid = s.create("u1", "私密.txt", b"secret", "text/plain")["id"]
    assert s.list("u2") == []                                # 他人看不到
    assert s.get("u2", rid) is None                          # 他人取不到
    assert s.delete("u2", rid) is False                      # 他人删不掉
    assert s.get("u1", rid) is not None                      # 本人仍在


def test_same_name_no_overwrite(tmp_path):
    s = _store(tmp_path)
    a = s.create("u1", "同名.txt", b"AAAA", "text/plain")
    b = s.create("u1", "同名.txt", b"BB", "text/plain")
    assert a["id"] != b["id"]                                # 各自独立 id
    with open(s.path(a["id"]), "rb") as f: assert f.read() == b"AAAA"
    with open(s.path(b["id"]), "rb") as f: assert f.read() == b"BB"


# ---------- 重复生成去重 ----------

def test_same_file_saved_twice_is_deduped(tmp_path):
    """回归：编排器单步重试会把 save_download 原样再调一遍（max_step_retry=2 即初次+1 次
    重试），同一份笔记被存两次，消息下方冒出两个一模一样的下载按钮。"""
    s = DownloadStore(str(tmp_path), ":memory:")
    a = s.create("u1", "学习笔记.md", "# AI\n内容".encode(), "text/markdown")
    b = s.create("u1", "学习笔记.md", "# AI\n内容".encode(), "text/markdown")
    assert a["id"] == b["id"]
    assert len(s.list("u1")) == 1


def test_changed_content_is_a_new_file(tmp_path):
    """内容变了就是新文件——重试后模型改进了笔记，不能被误合并成旧版。"""
    s = DownloadStore(str(tmp_path), ":memory:")
    a = s.create("u1", "笔记.md", b"v1", "text/markdown")
    b = s.create("u1", "笔记.md", b"v2", "text/markdown")
    assert a["id"] != b["id"] and len(s.list("u1")) == 2


def test_same_content_different_name_deduped(tmp_path):
    """同内容不同名也算重复——判重键刻意不含文件名。

    文件名由模型自拟，两次拟得一字不差才算重复的话这道去重形同虚设；而工具描述恰恰要求
    文件名「写清主题、别用泛称」，等于在鼓励它每次换个说法。实测症状就是「生成内容」步和
    「保存文件」步各存一份同样的内容、名字略有出入，下载区并排两个按钮。
    内容才是文件的身份。先存的那个名字保留。
    """
    s = DownloadStore(str(tmp_path), ":memory:")
    a = s.create("u1", "甲.md", b"same", "text/markdown")
    b = s.create("u1", "乙.md", b"same", "text/markdown")
    assert a["id"] == b["id"]
    assert b["filename"] == "甲.md"          # 保留先存的名字，不被后来者改写
    assert len(s.list("u1")) == 1


def test_dedup_is_per_user(tmp_path):
    """跨用户绝不能复用同一条记录——那会把 A 的文件泄露给 B。"""
    s = DownloadStore(str(tmp_path), ":memory:")
    a = s.create("u1", "笔记.md", b"same", "text/markdown")
    b = s.create("u2", "笔记.md", b"same", "text/markdown")
    assert a["id"] != b["id"]
    assert len(s.list("u1")) == 1 and len(s.list("u2")) == 1
