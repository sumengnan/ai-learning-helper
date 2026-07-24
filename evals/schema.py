# evals/schema.py
"""eval 数据集的 case schema。JSONL 一行一 case，kind 判别 payload。

端到端 agent case 与组件级 case 共用同一个 envelope（id/tags/notes），只有 input/expect
的形状按 kind 分化 —— 一个 loader、一个 report、一个 baseline、一个 CLI。分两套格式就要
分两套全家桶，那才是过度设计。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

# 脚本化假 judge 的特殊返回值（见 evals/drivers.py::scripted_complete）。
# 用它们把「基建挂了」「模型不吐 JSON」这两种线上真实故障写进数据集当回归 case。
RAISE = "__raise__"
BAD_JSON = "__bad_json__"


class _Base(BaseModel):
    id: str
    tags: list[str] = []
    notes: str = ""


# ---------- 端到端 agent（慢层，真实 API） ----------

class AgentInput(BaseModel):
    message: str
    system_prompt: str | None = None       # None 则用注入的默认系统提示


class AgentExpect(BaseModel):
    must_contain: list[str] = []
    must_not_contain: list[str] = []
    must_call_tools: list[str] = []        # 轨迹断言：这些工具必须被调过
    rubric: str = ""                       # 评分要点，拼进 judge 的 user prompt
    min_judge_score: int = 70


class AgentCase(_Base):
    kind: Literal["agent"] = "agent"
    input: AgentInput
    expect: AgentExpect


# ---------- 组件：exam_grader 判分准确率 ----------

class ExamInput(BaseModel):
    question: dict                         # 与 QuestionStore 同构：type/stem/options/answer
    user_text: str                         # 用户自由文本作答
    # 简答题用：按 system prompt 关键词分派的假 judge 返回。值可为 RAISE / BAD_JSON。
    judge_returns: dict[str, Any] = {}


class ExamExpect(BaseModel):
    # 两个字段都用「写了才校验」语义（靠 pydantic 的 model_fields_set 判断是否写了），
    # 因为 None 本身就是合法期望值：parsed=None 表示「期望解析不出/有歧义」。
    parsed: Any = None                     # 期望 parse_choice 的产物
    correct: bool | None = None            # 期望 grade_objective / grade_short 的判定


class ExamGradeCase(_Base):
    kind: Literal["exam_grade"] = "exam_grade"
    input: ExamInput
    expect: ExamExpect


# ---------- 组件：交付后机械检查（提醒型）准召 ----------

class ChecksInput(BaseModel):
    answer: str
    grounding: list[dict] = []             # 同 chat.py collect["grounding"] 形状
    judge_returns: dict[str, Any] = {}     # {system 关键词: 假 JSON}，值可为 RAISE / BAD_JSON
    config_overrides: dict = {}            # delivery_check_* 等
    # 桩代码工具 {工具名: "ok"|"fail"}，供 code 检查的 case 用。
    # 空 dict → registry 为 None → code/facts 检查自动跳过。
    stub_tools: dict[str, str] = {}


class ChecksExpect(BaseModel):
    # 期望产出的提醒项（kind 列表，子集匹配）。空列表 = 期望一条提醒都不产生。
    notices: list[str] = []


class ChecksCase(_Base):
    kind: Literal["checks"] = "checks"
    input: ChecksInput
    expect: ChecksExpect


# ---------- 组件：检索 ----------

class RetrievalInput(BaseModel):
    query: str
    corpus: str = "default"                # 语料集名 → evals/datasets/corpus/<name>.jsonl


class RetrievalExpect(BaseModel):
    relevant_ids: list[str]


class RetrievalCase(_Base):
    kind: Literal["retrieval"] = "retrieval"
    input: RetrievalInput
    expect: RetrievalExpect


EvalCase = Annotated[
    AgentCase | ChecksCase | ExamGradeCase | RetrievalCase,
    Field(discriminator="kind"),
]
