"""任务清单的确定性检测 + 定向收尾。

动机来自一条真实轨迹：模型只调了 2 次 update_plan，清单停在
[done, done, running, pending]，而第 4 步的 save_download 明明执行了、文件也生成了。
全库 19 个用过 update_plan 且正常完成的 run 里，3 个（16%）结束时清单没收尾。

三层校验都没发现：交付门 5 项只看答案文本；轨迹 judge 虽拿到了清单，却被问的是
「拆得合不合理」，于是对着那份半截清单打了 plan:95。用模型审模型的自述，发现不了
自述与事实的偏离——所以检测必须是确定性的。
"""
import json

from app.tools.plan_tool import merge_finalized, unfinished_steps

STALE = json.dumps([
    {"title": "检索技术演进", "status": "done"},
    {"title": "检索落地模式", "status": "done"},
    {"title": "整合信息", "status": "running"},
    {"title": "生成笔记", "status": "pending"},
])


# ---------- 检测：纯文本事实，零模型 ----------

def test_detects_unfinished():
    left = unfinished_steps(STALE)
    assert [s["title"] for s in left] == ["整合信息", "生成笔记"]


def test_clean_list_detects_nothing():
    clean = json.dumps([{"title": "a", "status": "done"},
                        {"title": "b", "status": "failed"}])
    assert unfinished_steps(clean) == []      # failed 也是终态，不该被当成没收尾


def test_garbage_never_raises():
    """清单是模型传的，脏数据不该让交付路径炸掉。"""
    for bad in ("", None, "not json", "{}", '"str"', "[1,2]"):
        assert unfinished_steps(bad) == []


# ---------- 合并：只许改 status，其余一律驳回 ----------

def test_merge_applies_terminal_status():
    merged = merge_finalized(STALE, [
        {"title": "检索技术演进", "status": "done"},
        {"title": "检索落地模式", "status": "done"},
        {"title": "整合信息", "status": "done"},
        {"title": "生成笔记", "status": "done"},
    ])
    assert [s["status"] for s in merged] == ["done"] * 4
    assert [s["title"] for s in merged] == ["检索技术演进", "检索落地模式", "整合信息", "生成笔记"]


def test_merge_keeps_original_titles_even_if_model_rewrites_them():
    """模型改写标题 → 用户会看到一份「不是自己那份」的清单，比不收尾更糟。
    标题以原清单为准，只取 status。"""
    merged = merge_finalized(STALE, [
        {"title": "改写了的标题A", "status": "done"},
        {"title": "改写了的标题B", "status": "done"},
        {"title": "改写了的标题C", "status": "done"},
        {"title": "改写了的标题D", "status": "failed"},
    ])
    assert [s["title"] for s in merged] == ["检索技术演进", "检索落地模式", "整合信息", "生成笔记"]
    assert merged[3]["status"] == "failed"


def test_merge_rejects_step_count_change():
    """模型增删步骤 → 整份驳回，宁可保留半截清单（前端会如实标『状态未知』）。"""
    assert merge_finalized(STALE, [{"title": "整合信息", "status": "done"}]) is None
    assert merge_finalized(STALE, [{"title": f"第{i}步", "status": "done"} for i in range(5)]) is None


def test_merge_rejects_non_terminal_status():
    """该收尾的还给 running → 没完成收尾这件事，不能算数。"""
    assert merge_finalized(STALE, [
        {"title": "检索技术演进", "status": "done"},
        {"title": "检索落地模式", "status": "done"},
        {"title": "整合信息", "status": "running"},   # 仍未收尾
        {"title": "生成笔记", "status": "done"},
    ]) is None


def test_merge_never_rewrites_already_terminal_steps():
    """已 done 的步骤是模型当时的现场判断，收尾员无权翻案（它没有那时的上下文）。"""
    merged = merge_finalized(STALE, [
        {"title": "检索技术演进", "status": "failed"},   # 企图把已 done 的改成 failed
        {"title": "检索落地模式", "status": "done"},
        {"title": "整合信息", "status": "done"},
        {"title": "生成笔记", "status": "done"},
    ])
    assert merged[0]["status"] == "done"      # 保持原样，不被翻案


def test_merge_rejects_garbage():
    assert merge_finalized(STALE, "not a list") is None
    assert merge_finalized("not json", [{"title": "a", "status": "done"}]) is None
    assert merge_finalized(STALE, [None, None, None, None]) is None
