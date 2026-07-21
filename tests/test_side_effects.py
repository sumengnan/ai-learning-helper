"""副作用产物的识别与清理（编排器单步重试与交付门重答共用）。"""
import pytest

from app.side_effects import SideEffectPurger, empty_fx, ids_from_tool, merge_fx


def test_ids_from_save_download():
    fx = ids_from_tool("save_download", "已保存 〔下载ID:d1〕", is_error=False)
    assert fx["download"] == ["d1"]
    assert fx["knowledge"] == [] and fx["questions"] == []


def test_ids_from_knowledge_and_questions():
    assert ids_from_tool("save_to_knowledge", "〔知识ID:k1〕", False)["knowledge"] == ["k1"]
    # 出题工具一次产出多个 id，逗号分隔在同一个标记里
    fx = ids_from_tool("generate_questions", "〔题目ID:q1,q2,q3〕", False)
    assert fx["questions"] == ["q1", "q2", "q3"]


def test_failed_tool_call_produces_nothing():
    # 工具报错时没有真产物，标记若混在报错文本里也不能当成要清理的东西
    assert ids_from_tool("save_download", "失败 〔下载ID:d1〕", is_error=True) == empty_fx()


def test_unknown_tool_produces_nothing():
    assert ids_from_tool("calculator", "〔下载ID:d1〕", False) == empty_fx()


def test_merge_accumulates_across_calls():
    a = ids_from_tool("save_download", "〔下载ID:d1〕", False)
    b = ids_from_tool("save_download", "〔下载ID:d2〕", False)
    assert merge_fx(a, b)["download"] == ["d1", "d2"]


def test_empty_fx_is_not_shared_between_callers():
    # empty_fx() 被当默认值到处传，若返回同一个可变对象，一处 append 会污染所有调用方
    a = ids_from_tool("calculator", "", False)
    a["download"].append("脏数据")
    assert ids_from_tool("calculator", "", False)["download"] == []


# ---------- SideEffectPurger ----------

class _FakeDownloads:
    def __init__(self): self.deleted = []
    def delete(self, uid, did): self.deleted.append((uid, did))


class _FakeKnowledge:
    def __init__(self): self.deleted = []
    def delete(self, uid, kid): self.deleted.append((uid, kid))


class _FakeQuestions:
    def __init__(self): self.deleted = []
    def delete_many(self, uid, ids): self.deleted.append((uid, list(ids)))


def _purger():
    dl, kb, q = _FakeDownloads(), _FakeKnowledge(), _FakeQuestions()
    return SideEffectPurger(download_store=dl, knowledge_service=kb, question_store=q), dl, kb, q


def test_purge_deletes_each_kind():
    p, dl, kb, q = _purger()
    purged = p.purge("u1", {"download": ["d1"], "knowledge": ["k1"], "questions": ["q1", "q2"]})
    assert dl.deleted == [("u1", "d1")]
    assert kb.deleted == [("u1", "k1")]
    assert q.deleted == [("u1", ["q1", "q2"])]
    assert purged == ["d1"]        # 返回被删下载 id，供通知在途前端撤掉按钮


def test_purge_on_empty_touches_nothing():
    p, dl, kb, q = _purger()
    assert p.purge("u1", empty_fx()) == []
    assert not dl.deleted and not kb.deleted and not q.deleted


def test_purge_survives_store_failure():
    """清理失败绝不能把整轮带崩——产物残留只是脏数据，抛异常会中断用户的这次回答。"""
    class _Boom:
        def delete(self, *a): raise RuntimeError("磁盘炸了")
    p = SideEffectPurger(download_store=_Boom(), knowledge_service=None, question_store=None)
    assert p.purge("u1", {"download": ["d1"], "knowledge": [], "questions": []}) == []


def test_purge_tolerates_missing_stores():
    # 精简装配（测试/无下载能力部署）下 store 可能为 None
    p = SideEffectPurger(download_store=None, knowledge_service=None, question_store=None)
    assert p.purge("u1", {"download": ["d1"], "knowledge": ["k1"], "questions": ["q1"]}) == []
