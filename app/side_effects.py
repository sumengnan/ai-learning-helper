# app/side_effects.py
"""副作用产物的识别与清理。

「副作用工具」指调用后在系统里留下**用户可见产物**的工具：save_download 落一份可下载
文件、save_to_knowledge 往知识库塞一条、add_questions/generate_questions 入库题目。
这些工具在结果文本里带机读标记（〔下载ID:x〕之类），本模块负责把标记扒出来、并在
某次尝试作废时把对应产物删掉。

为什么需要：编排器的单步校验不过会重跑该步，模型多半会把带副作用的工具再调一遍。
不清理的话同一份产物在库里留两份，聊天下方冒出两个下载按钮，其中一个还是被判为
不合格的那版。此前唯一的缓解是 DownloadStore.create 按 (user, filename, sha256) 去重，
但重试的目的正是让产出变得不一样——内容一变去重就失效，双按钮照旧。

本模块从 api/chat.py 的私有实现提出来共用：那边的交付门重答路径也做同一件事，只是
当前主流程恒建 orchestrator，那条分支实际跑不到。
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("app.side_effects")

_DL_ID_RE = re.compile(r"〔下载ID:([^〕]+)〕")
_KB_ID_RE = re.compile(r"〔知识ID:([^〕]+)〕")
_Q_ID_RE = re.compile(r"〔题目ID:([^〕]+)〕")

KINDS = ("download", "knowledge", "questions")

def empty_fx() -> dict[str, list[str]]:
    """每次都返回**新的**可变 dict。刻意不做成模块级常量：它会被当默认值到处传，
    一旦有调用方就地 append，污染会顺着共享引用扩散到所有人。"""
    return {k: [] for k in KINDS}


def ids_from_tool(tool: str, result: str, is_error: bool) -> dict[str, list[str]]:
    """从单次工具调用的结果里提取产物 id。非副作用工具、报错的调用一律返回空。

    **与 executor 里 effects 的判定口径不同，两者会漂**：effects 用「结果带 marker」
    （model_content 非空）通用判定，本函数用硬编码工具名单 + 正则。当前两边覆盖的集合
    并不完全重合——出题工具把〔题目ID:x〕拼在正文里而非 marker 字段（见 exam_tools.py），
    故只被本函数记到、不被 effects 记到。今天这个差异恰好落在安全的一侧（删了题目、
    模型也没被告知"已做过"，于是会重出），但反过来就不安全：新增一个走 marker 却不在
    下面名单里的工具，effects 会告诉模型"别重做"，而本函数扒不到 id、产物删不掉——
    用户拿到的是被判不合格那版的产物。加新的副作用工具时，这两处要一起改。

    报错必须排除：工具失败时没有真产物，而报错文本里可能回显了标记，
    照扒会去删一个不存在（或属于别人）的 id。
    """
    out = empty_fx()
    if is_error or not result:
        return out
    if tool == "save_download":
        out["download"] += _DL_ID_RE.findall(result)
    elif tool == "save_to_knowledge":
        out["knowledge"] += _KB_ID_RE.findall(result)
    elif tool in ("add_questions", "generate_questions"):
        for grp in _Q_ID_RE.findall(result):
            out["questions"] += [x for x in grp.split(",") if x]
    return out


def merge_fx(*fxs) -> dict[str, list[str]]:
    """合并多次调用的产物清单。"""
    out = empty_fx()
    for fx in fxs:
        for k in KINDS:
            out[k] += list(fx.get(k) or ())
    return out


def has_any(fx) -> bool:
    return any(fx.get(k) for k in KINDS)


class SideEffectPurger:
    """按产物 id 删除对应的下载文件 / 知识条目 / 题目。

    store 允许为 None：精简装配（测试、未开下载能力的部署）下这些能力可能压根没接。
    """

    def __init__(self, *, download_store=None, knowledge_service=None,
                 question_store=None) -> None:
        self._downloads = download_store
        self._knowledge = knowledge_service
        self._questions = question_store

    def purge(self, user_id: str, fx) -> dict[str, list[str]]:
        """删掉这批产物，返回**确实删掉的** id（按类分组）。

        只报真删掉的，是因为调用方拿这个结果去剥消息里的产物标记、并标注"已作废删除"。
        把没删成的也算进去，会造成静默的数据不一致：磁盘上文件还在、库里行还在，
        用户的下载入口却没了。store.delete 返回 False（不存在/不属于该用户）同样不算。

        任何一处删除失败都只记日志、不抛：清理是善后动作，产物残留至多是脏数据，
        而抛异常会中断用户正在进行的这次回答——两害相权取其轻。
        """
        done = empty_fx()
        for did in fx.get("download") or ():
            if self._downloads is None:
                continue
            try:
                if self._downloads.delete(user_id, did) is not False:
                    done["download"].append(did)
            except Exception:
                log.warning("清理下载产物失败 user=%s id=%s", user_id, did, exc_info=True)
        for kid in fx.get("knowledge") or ():
            if self._knowledge is None:
                continue
            try:
                if self._knowledge.delete(user_id, kid) is not False:
                    done["knowledge"].append(kid)
            except Exception:
                log.warning("清理知识条目失败 user=%s id=%s", user_id, kid, exc_info=True)
        qids = list(fx.get("questions") or ())
        if qids and self._questions is not None:
            try:
                self._questions.delete_many(user_id, qids)
                done["questions"] = qids     # 批量接口不逐条回报，全成或全败
            except Exception:
                log.warning("清理题目失败 user=%s n=%d", user_id, len(qids), exc_info=True)
        return done


# 产物类别 → 产出它的工具名。用于「删了就让模型重做」：某类产物被清理后，
# 要把这些工具名从该步的 done_effects 里摘掉，否则重跑时模型仍被告知
# 「你已经做过了」，于是不再保存——旧的删了、新的没生成，用户手里一个都不剩。
TOOLS_BY_KIND = {
    "download": ("save_download",),
    "knowledge": ("save_to_knowledge",),
    "questions": ("add_questions", "generate_questions"),
}


def tools_to_redo(purged) -> set[str]:
    """按实际清理掉的产物，算出「该让模型重做」的工具名集合。

    入参是**实际删掉的**清单而非「想删的」：删除失败时产物还在，
    再让模型存一遍就真的成了两份。
    """
    return {t for kind, ids in (purged or {}).items() if ids
            for t in TOOLS_BY_KIND.get(kind, ())}
