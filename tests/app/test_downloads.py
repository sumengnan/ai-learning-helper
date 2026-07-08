import os
from app.downloads import DownloadStore


def _store(tmp_path):
    return DownloadStore(str(tmp_path / "files"), ":memory:")


def test_create_writes_file_and_registers(tmp_path):
    s = _store(tmp_path)
    rec = s.create("笔记.md", b"# hi", "text/markdown")
    assert rec["filename"] == "笔记.md" and rec["size"] == 4
    assert rec["content_type"] == "text/markdown"
    assert os.path.exists(s.path(rec["id"]))                 # 落盘
    with open(s.path(rec["id"]), "rb") as f:
        assert f.read() == b"# hi"


def test_list_newest_first(tmp_path):
    s = _store(tmp_path)
    a = s.create("a.txt", b"a", "text/plain")["id"]
    b = s.create("b.txt", b"b", "text/plain")["id"]
    assert [r["id"] for r in s.list()] == [b, a]             # 倒序


def test_get_includes_path_and_none_missing(tmp_path):
    s = _store(tmp_path)
    rid = s.create("x.txt", b"x", "text/plain")["id"]
    got = s.get(rid)
    assert got["path"] == s.path(rid) and got["filename"] == "x.txt"
    assert s.get("nope") is None


def test_delete_removes_file_and_row(tmp_path):
    s = _store(tmp_path)
    rid = s.create("x.txt", b"x", "text/plain")["id"]
    p = s.path(rid)
    assert s.delete(rid) is True
    assert not os.path.exists(p)                             # 文件删了
    assert s.get(rid) is None                                # 登记删了
    assert s.delete(rid) is False                            # 再删不存在


def test_same_name_no_overwrite(tmp_path):
    s = _store(tmp_path)
    a = s.create("同名.txt", b"AAAA", "text/plain")
    b = s.create("同名.txt", b"BB", "text/plain")
    assert a["id"] != b["id"]                                # 各自独立 id
    with open(s.path(a["id"]), "rb") as f: assert f.read() == b"AAAA"
    with open(s.path(b["id"]), "rb") as f: assert f.read() == b"BB"
