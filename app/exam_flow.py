# app/exam_flow.py
"""考试判分中间件的核心编排：把用户消息当作对「当前题」的作答，服务端判分、
（答错）确定性存错题集、推进游标，并产出注入模型的判定提示。

判分与保存全在这里发生，不依赖模型调用任何工具——模型只按注入的判定去讲解 / 呈现下一题。
"""
from __future__ import annotations

from .exam_grader import (
    END_INTENT_RE, answer_text, grade_objective, grade_short,
    parse_choice, present_question)
from .exam_session import ExamSessionStore


def _snapshot(q: dict) -> dict:
    return {"type": q["type"], "stem": q["stem"], "options": q.get("options"),
            "answer": q.get("answer"), "explanation": q.get("explanation", "")}


def _display_answer(q: dict, user_answer) -> str:
    """用户作答的人读文本，供注入提示帮模型讲评。"""
    typ, opts = q["type"], q.get("options") or []
    if typ == "truefalse":
        return "对" if user_answer else "错"
    if typ == "single" and isinstance(user_answer, int) and 0 <= user_answer < len(opts):
        return f"{chr(65 + user_answer)}. {opts[user_answer]}"
    if typ == "multiple" and isinstance(user_answer, list):
        return "、".join(f"{chr(65 + i)}. {opts[i]}" if 0 <= i < len(opts) else str(i)
                        for i in user_answer)
    return str(user_answer)


def _graded_summary(questions: list[dict], results: list[dict], n: int) -> str:
    correct_n = sum(1 for r in results if r["is_correct"])
    lines = []
    for i, (qq, r) in enumerate(zip(questions, results)):
        mark = "✓" if r["is_correct"] else "✗"
        lines.append(f"{i + 1}. {mark} 正确答案：{answer_text(qq)}")
    return (f"\n\n[考试系统] 全部 {n} 题作答完毕，考试结束。得分 {correct_n}/{n}。逐题结果：\n"
            + "\n".join(lines)
            + "\n请向用户公布得分并逐题讲解（重点讲答错的题）。")


def _build_note(mode: str, q: dict, idx: int, n: int, is_correct: bool,
                user_answer, saved: bool, jfb: str, finished: bool, after: dict) -> str:
    user_txt = _display_answer(q, user_answer)
    if mode == "graded":
        if finished:
            return _graded_summary(after["questions"], after["results"], n)
        nxt = present_question(ExamSessionStore.current(after), after["cursor"], n)
        return (f"\n\n[考试系统] 已记录用户对第 {idx + 1}/{n} 题的作答（{user_txt}）。"
                f"打分式考试进行中，【不要】向用户透露本题对错或正确答案。"
                f"请直接呈现下一题：\n{nxt}")

    # instant（即时式）
    verdict = "答对 ✓" if is_correct else "答错 ✗"
    parts = [f"\n\n[考试系统判定] 第 {idx + 1}/{n} 题：用户{verdict}。"
             f"用户作答：{user_txt}。正确答案：{answer_text(q)}。"]
    if q.get("explanation"):
        parts.append(f"参考解析：{q['explanation']}")
    if q["type"] == "short" and jfb:
        parts.append(f"判分参考：{jfb}")
    if saved:
        parts.append("★本题答错，已由系统自动存入用户的「错题集」。请在给用户的讲解里"
                     "【明确告诉用户】「这道题已加入你的错题集，方便以后复习」，让用户清楚知道，不要略过。")
    if finished:
        parts.append("这是最后一题，考试到此结束。请据以上向用户讲解本题，再做简短总结。")
    else:
        nxt = present_question(ExamSessionStore.current(after), after["cursor"], n)
        parts.append("请据以上向用户讲解本题（是否答对、正确答案、简要原因），"
                     f"然后呈现下一题：\n{nxt}")
    return "\n".join(parts)


async def grade_exam_turn(exam_store, wrong_store, judge_complete, *,
                          user_id: str, conv_id: str, message: str,
                          save_wrong: bool) -> tuple[str, bool]:
    """返回 (注入模型的判定提示, 考试是否仍活跃)。无 active 考试返回 ('', False)。"""
    exam = exam_store.get_active(user_id, conv_id)
    if exam is None:
        return "", False

    if END_INTENT_RE.search(message or ""):        # 结束意图 → 结束，不判分
        exam_store.end(user_id, conv_id)
        return ("\n\n[考试系统] 用户要求结束考试，考试已结束。请给出简短小结。", False)

    q = ExamSessionStore.current(exam)
    if q is None:                                  # 防御：已答完未结束
        exam_store.end(user_id, conv_id)
        return "", False

    mode, n, idx = exam["mode"], len(exam["questions"]), exam["cursor"]

    if q["type"] == "short":
        is_correct, jfb = await grade_short(judge_complete, q, message)
        user_answer = message
    else:
        parsed = parse_choice(message, q)
        if parsed is None:                         # 客观题无法识别 → 不判/不存/不推进
            return (f"\n\n[考试系统] 未能识别用户对第 {idx + 1} 题的作答。"
                    f"请提示用户明确作答（如选项字母 A/B 或 对/错），本题暂不推进。", True)
        is_correct, jfb = grade_objective(q, parsed), ""
        user_answer = parsed

    saved = False
    if (not is_correct) and save_wrong and wrong_store is not None:
        wrong_store.create(user_id, q.get("question_id", "") or "", "exam",
                           _snapshot(q), user_answer)      # 确定性保存
        saved = True

    exam_store.record(user_id, conv_id, user_answer, is_correct)
    after = exam_store.get_active(user_id, conv_id)         # 游标已推进
    finished = after is None or ExamSessionStore.is_finished(after)
    note = _build_note(mode, q, idx, n, is_correct, user_answer, saved, jfb, finished, after)
    if finished:
        exam_store.end(user_id, conv_id)
    return note, (not finished)
