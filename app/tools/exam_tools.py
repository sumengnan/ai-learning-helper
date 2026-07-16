# app/tools/exam_tools.py
from __future__ import annotations

import json
from typing import Annotated

from pydantic import BaseModel, BeforeValidator

from harness.tools.base import Tool

from ..quiz_service import NoKnowledge, QuizError, _valid

# 出题合法性校验允许的全部题型（供 add_questions 逐题校验用）
ALL_TYPES = ["single", "multiple", "truefalse", "short"]


def _coerce_json_list(v):
    """模型有时把数组参数序列化成 JSON 字符串（可能带首尾空白/换行）再传，容错解析成列表；
    解析不了就原样返回，交给后续类型校验报清晰错误。"""
    if isinstance(v, str):
        try:
            return json.loads(v.strip())
        except (ValueError, TypeError):
            return v
    return v


# list 型工具参数：容忍模型传 JSON 字符串
_QuestionList = Annotated[list[dict], BeforeValidator(_coerce_json_list)]
_IdList = Annotated[list[str], BeforeValidator(_coerce_json_list)]
_OptIdList = Annotated[list[str] | None, BeforeValidator(_coerce_json_list)]


def _clamp(count: int) -> int:
    return max(1, min(count, 50))


class SampleQuestionsTool(Tool):
    name = "sample_questions"
    description = (
        "从当前用户的题库中随机抽取题目用于模拟考试。返回的题目含答案与解析，"
        "仅供你出题与判分：在『打分式』考试中，作答完成前不要向用户透露答案。")

    class Params(BaseModel):
        count: int = 5
        types: _OptIdList = None

    def __init__(self, question_store, user_id: str) -> None:
        self._store = question_store
        self._uid = user_id

    async def run(self, params: "SampleQuestionsTool.Params") -> str:
        count = max(1, min(params.count, 50))
        qs = self._store.sample(self._uid, count, params.types)
        if not qs:
            return "题库为空。请提醒用户先到「题库」页面生成题目，或改用其他方式练习。"
        return json.dumps(qs, ensure_ascii=False)


class SaveWrongAnswerTool(Tool):
    name = "save_wrong_answer"
    description = (
        "把用户答错的题目保存到「错题集」。当用户某题作答错误时调用。"
        "优先直接传该题内容：stem（题干）、type（single/multiple/truefalse/short）、"
        "answer（正确答案）、options（选项，可空）、explanation（解析，可空），"
        "以及用户的作答 user_answer。若该题来自题库（sample_questions 返回的），"
        "也可只传其 question_id 让系统取快照。"
        "即席出题（题目不在题库）时务必传题目内容，不能只靠 question_id。")

    class Params(BaseModel):
        question_id: str = ""          # 题库题目 id；即席出题留空
        stem: str = ""
        type: str = ""
        answer: object = None
        options: object = None
        explanation: str = ""
        user_answer: object = None

    def __init__(self, question_store, wrong_store, user_id: str) -> None:
        self._qs = question_store
        self._ws = wrong_store
        self._uid = user_id

    async def run(self, params: "SaveWrongAnswerTool.Params") -> str:
        snapshot = None
        if params.question_id:                       # 题库题：按 id 取快照
            q = self._qs.get(self._uid, params.question_id)
            if q is not None:
                snapshot = {"type": q["type"], "stem": q["stem"], "options": q["options"],
                            "answer": q["answer"], "explanation": q["explanation"]}
        if snapshot is None and (params.stem or "").strip():   # 即席题：用传入内容
            snapshot = {"type": params.type or "short", "stem": params.stem,
                        "options": params.options, "answer": params.answer,
                        "explanation": params.explanation or ""}
        if snapshot is None:
            return ("无法保存到错题集：请直接提供题目内容（至少 stem 与 answer）；"
                    "即席出题时不能只靠 question_id。")
        self._ws.create(self._uid, params.question_id or "", "chat", snapshot,
                        params.user_answer)
        return "已保存到错题集。"


class AddQuestionsTool(Tool):
    name = "add_questions"
    description = (
        "把对话里现成的知识/资料整理成题目，直接存入用户的「题库」（不经知识库检索）。"
        "适用于用户说「把这些整理成题存进题库」等场景。"
        "questions 为题目数组，每题形如："
        "{\"type\":\"single|multiple|truefalse|short\",\"stem\":\"题干\","
        "\"options\":[\"选项\"]或null,\"answer\":单选为选项索引整数/多选为索引数组/"
        "判断为true或false/简答为参考答案字符串,\"explanation\":\"解析\"}。"
        "非法题目会被跳过。")

    class Params(BaseModel):
        questions: _QuestionList

    def __init__(self, question_store, user_id: str) -> None:
        self._store = question_store
        self._uid = user_id

    async def run(self, params: "AddQuestionsTool.Params") -> str:
        valid = [q for q in params.questions if _valid(q, ALL_TYPES)]
        added_ids: list[str] = []
        for q in valid:
            q.setdefault("source", "聊天整理")
            q["explanation"] = q.get("explanation", "")
            qid = self._store.create_deduped(self._uid, q)
            if qid is not None:
                added_ids.append(qid)
        added = len(added_ids)
        skipped = len(params.questions) - added
        if added == 0:
            return f"没有新题入库（跳过 {skipped} 道：无效或与题库重复）。"
        # 末尾带机读标记〔题目ID:id,id〕：交付门据此在校验不通过时清理该轮误入库的题（前端剥离不展示）
        return (f"已入库 {added} 道，跳过 {skipped} 道（无效或重复）。"
                f"〔题目ID:{','.join(added_ids)}〕")


class GenerateQuestionsTool(Tool):
    name = "generate_questions"
    description = (
        "从用户「知识库」里就某主题自动检索资料并出题，出好的题直接存入题库。"
        "适用于用户说「就 X 主题从我的知识库出几道题」的场景。"
        "topic 为主题，count 为题数，types 可限定题型（single/multiple/truefalse/short）。"
        "若知识库无相关内容会返回提示。")

    class Params(BaseModel):
        topic: str
        count: int = 5
        types: _OptIdList = None

    def __init__(self, quiz_service, user_id: str) -> None:
        self._quiz = quiz_service
        self._uid = user_id

    async def run(self, params: "GenerateQuestionsTool.Params") -> str:
        types = params.types or ALL_TYPES
        try:
            qs = await self._quiz.generate(
                self._uid, params.topic, _clamp(params.count), types)
        except NoKnowledge:
            return (f"知识库里没有『{params.topic}』相关内容，无法出题。"
                    "请先把资料存入知识库，或改用 add_questions 直接整理入库。")
        except QuizError:
            return "出题失败：生成结果无有效题目，请调整主题或稍后重试。"
        ids = [q["id"] for q in qs if q.get("id")]
        msg = f"已从知识库生成并入库 {len(qs)} 道题（主题：{params.topic}）。"
        # 末尾带机读标记〔题目ID:id,id〕：交付门据此清理失败轮误入库的题（前端剥离不展示）；
        # 也让后续「就考刚才这几道」能用 start_exam(source=ids) 精确取到这批题
        return f"{msg}〔题目ID:{','.join(ids)}〕" if ids else msg


class ListQuestionsTool(Tool):
    name = "list_questions"
    description = (
        "列出用户题库中的题目（返回 id、题型、题干，不含答案），"
        "用于向用户展示题库或在删除前确认要删哪些题。types 可选，限定题型。")

    class Params(BaseModel):
        types: _OptIdList = None

    def __init__(self, question_store, user_id: str) -> None:
        self._store = question_store
        self._uid = user_id

    async def run(self, params: "ListQuestionsTool.Params") -> str:
        qs = self._store.list(self._uid)
        if params.types:
            qs = [q for q in qs if q["type"] in params.types]
        if not qs:
            return "题库为空。"
        brief = [{"id": q["id"], "type": q["type"], "stem": q["stem"]} for q in qs]
        return json.dumps(brief, ensure_ascii=False)


class DeleteQuestionsTool(Tool):
    name = "delete_questions"
    description = (
        "从用户题库中删除指定题目（按 question_id）。删除不可恢复，"
        "调用前必须先向用户复述将删除的题目并取得确认。"
        "question_id 可先用 list_questions 获取。")

    class Params(BaseModel):
        question_ids: _IdList

    def __init__(self, question_store, user_id: str) -> None:
        self._store = question_store
        self._uid = user_id

    async def run(self, params: "DeleteQuestionsTool.Params") -> str:
        before = {q["id"] for q in self._store.list(self._uid)}
        hit = [i for i in params.question_ids if i in before]
        self._store.delete_many(self._uid, hit)
        return f"已从题库删除 {len(hit)} 道题。"


class SampleWrongAnswersTool(Tool):
    name = "sample_wrong_answers"
    description = (
        "从用户的「错题集」随机抽取题目用于重考/复习。返回题目快照（含答案与解析）"
        "及错题 id，仅供你出题与判分：在『打分式』考试中作答完成前不要透露答案。")

    class Params(BaseModel):
        count: int = 5

    def __init__(self, wrong_store, user_id: str) -> None:
        self._store = wrong_store
        self._uid = user_id

    async def run(self, params: "SampleWrongAnswersTool.Params") -> str:
        rows = self._store.sample(self._uid, _clamp(params.count))
        if not rows:
            return "错题集为空。可先做题，答错的题会进入错题集。"
        out = [{"id": r["id"], "snapshot": r["snapshot"]} for r in rows]
        return json.dumps(out, ensure_ascii=False)


class DeleteWrongAnswersTool(Tool):
    name = "delete_wrong_answers"
    description = (
        "从用户「错题集」删除指定错题（按 wrong_answer_id，即 sample_wrong_answers 返回的 id）。"
        "删除不可恢复，调用前必须先向用户复述将删除的题目并取得确认。")

    class Params(BaseModel):
        wrong_answer_ids: _IdList

    def __init__(self, wrong_store, user_id: str) -> None:
        self._store = wrong_store
        self._uid = user_id

    async def run(self, params: "DeleteWrongAnswersTool.Params") -> str:
        before = {r["id"] for r in self._store.list(self._uid)}
        hit = [i for i in params.wrong_answer_ids if i in before]
        self._store.delete_many(self._uid, hit)
        return f"已从错题集删除 {len(hit)} 道题。"


class StartExamTool(Tool):
    name = "start_exam"
    description = (
        "开始一场由系统托管的模拟考试。开考后，每题的判分与「答错自动入错题集」都由系统"
        "在后台确定性完成——你【无需也不要】再调用 save_wrong_answer。你只负责呈现题目、"
        "并在系统给出判定后讲解。\n"
        "source：题源，bank=从题库随机抽题、ids=只考指定的题库题目、"
        "wrong=从错题集抽题重考、adhoc=你现编题目考。\n"
        "当用户指名要考某几道题（如「刚才生成的那几道」「就考这几题」）时【必须】用 "
        "source=ids 并在 question_ids 传入那些题的 id，按传入顺序出题；"
        "此时 count 与 types 忽略。题目 id 来自 add_questions/generate_questions 返回的"
        "〔题目ID:...〕标记，或 list_questions。切勿改用 bank——那是全库随机抽，考的不会是指定的题。\n"
        "adhoc 时【必须】在 questions 传入题目（含答案），每题形如 "
        "{\"type\":\"single|multiple|truefalse|short\",\"stem\":\"题干\","
        "\"options\":[\"选项\"]或null,\"answer\":单选选项索引/多选索引数组/判断true或false/简答参考答案,"
        "\"explanation\":\"解析\"}。\n"
        "mode：instant=即时式（每题即判即讲）、graded=打分式（全部答完再公布得分与讲解）。")

    class Params(BaseModel):
        source: str = "bank"
        count: int = 5
        types: _OptIdList = None
        mode: str = "instant"
        questions: _QuestionList | None = None
        question_ids: _OptIdList = None

    def __init__(self, exam_store, user_id: str, conv_id: str,
                 question_store=None, wrong_store=None) -> None:
        self._exam = exam_store
        self._qs = question_store
        self._ws = wrong_store
        self._uid = user_id
        self._conv = conv_id

    async def run(self, params: "StartExamTool.Params") -> str:
        from ..exam_grader import present_question

        mode = "graded" if params.mode == "graded" else "instant"
        count = _clamp(params.count)
        questions: list[dict] = []
        note = ""
        if params.source == "ids":
            if self._qs is None:
                return "题库不可用。"
            ids = [i for i in (params.question_ids or []) if i][:50]
            if not ids:
                return "无法开始考试：source=ids 需在 question_ids 传入至少一道题的 id。"
            questions = self._qs.get_many(self._uid, ids)
            if not questions:
                return ("无法开始考试：question_ids 里没有一道能在题库中找到（可能已被删除）。"
                        "可用 list_questions 核对题目 id，不要改用 bank 随机抽题冒充。")
            found = {q["id"] for q in questions}
            if len(found) < len(set(ids)):
                # 少考了题必须让用户知道，否则「考刚才那几道」会静默变成考其中几道
                note = (f"注意：指定的 {len(set(ids))} 道题中有 "
                        f"{len(set(ids)) - len(found)} 道在题库中找不到（可能已被删除），"
                        f"本场只考找到的 {len(questions)} 道。请如实告知用户。\n\n")
        elif params.source == "adhoc":
            for q in (params.questions or []):
                if _valid(q, ALL_TYPES):
                    questions.append({"type": q["type"], "stem": q["stem"],
                                      "options": q.get("options"), "answer": q.get("answer"),
                                      "explanation": q.get("explanation", "")})
            if not questions:
                return "无法开始考试：adhoc 模式需在 questions 里传入至少一道合法题目（含答案）。"
        elif params.source == "wrong":
            if self._ws is None:
                return "错题集不可用。"
            rows = self._ws.sample(self._uid, count)
            questions = [r["snapshot"] for r in rows]
            if not questions:
                return "错题集为空，无法用错题重考。可先做题，答错的题会进入错题集。"
        else:  # bank
            if self._qs is None:
                return "题库不可用。"
            questions = self._qs.sample(self._uid, count, params.types)
            if not questions:
                return ("题库为空，无法抽题考试。可先到「题库」生成题目，"
                        "或让我用 add_questions 整理入库、或用 source=adhoc 现编题考你。")

        self._exam.start(self._uid, self._conv, questions, mode)
        mode_zh = "打分式" if mode == "graded" else "即时式"
        first = present_question(questions[0], 0, len(questions))
        return (f"{note}已开始考试：共 {len(questions)} 题，{mode_zh}。请把下面第一题原样呈现给用户，"
                f"等其作答；判分与错题保存由系统自动完成。\n\n{first}")
