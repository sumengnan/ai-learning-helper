# app/conversations.py
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from harness.persistence.serialize import message_from_dict, message_to_dict
from harness.types import Message, Role, ToolCall

from .db import migrate, open_db


def _load_steps(steps_json: str) -> list[dict]:
    """解析 steps 列 JSON；坏数据/非列表一律当空，绝不影响历史回放。"""
    try:
        v = json.loads(steps_json)
    except (ValueError, TypeError):
        return []
    return [s for s in v if isinstance(s, dict)] if isinstance(v, list) else []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    def __init__(self, db_path: str | None = None, *,
                 conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self._conn = conn
        else:
            self._conn = open_db(db_path)
            migrate(self._conn)

    def create(self, user_id: str, title: str = "新对话") -> str:
        cid = uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO conversations(id, user_id, title, created_at) VALUES (?, ?, ?, ?)",
            (cid, user_id, title, _now()))
        self._conn.commit()
        return cid

    def list(self, user_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, title, created_at FROM conversations WHERE user_id = ? "
            "ORDER BY created_at DESC", (user_id,)).fetchall()
        return [{"id": r[0], "title": r[1], "created_at": r[2]} for r in rows]

    def exists(self, user_id: str, conv_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id)).fetchone() is not None

    def messages(self, conv_id: str) -> list[Message]:
        """回放为 LLM 历史。助手轮若带工具轨迹(steps)，先回放「工具调用+结果」消息、
        再放最终答复——使下一轮模型能看到本轮工具产出（如已抽的题目/检索结果），
        不必重复调用工具（否则多轮考试会每轮重新抽题、从头开始）。"""
        rows = self._conn.execute(
            "SELECT role, content, tool_calls, tool_call_id, steps FROM conversation_messages "
            "WHERE conv_id = ? ORDER BY seq", (conv_id,)).fetchall()
        out: list[Message] = []
        for i, (role, content, tool_calls, tool_call_id, steps) in enumerate(rows):
            if role == "assistant" and steps:
                for j, st in enumerate(_load_steps(steps)):
                    cid = f"h{i}_{j}"        # 合成 id：assistant.tool_calls 与 tool 消息配对
                    out.append(Message(role=Role.ASSISTANT, tool_calls=[
                        ToolCall(id=cid, name=st.get("tool", ""),
                                 arguments=st.get("args") or {})]))
                    out.append(Message(role=Role.TOOL, tool_call_id=cid,
                                       content=str(st.get("result") or "")))
            out.append(message_from_dict({
                "role": role, "content": content,
                "tool_calls": json.loads(tool_calls) if tool_calls else [],
                "tool_call_id": tool_call_id,
            }))
        return out

    def append(self, conv_id: str, msgs: list[Message],
               steps: list[dict] | None = None,
               progress: list[dict] | None = None,
               attachments: list[dict] | None = None) -> None:
        """追加消息。steps 为纯 UI 用途的工具调用轨迹（tool/args/result/is_error），
        progress 为沙箱/子代理执行的进度轨迹（scope/text/status/key）；两者都挂在本批
        最后一条（助手）消息上。attachments（id/filename/size/content_type）为本轮用户
        上传的附件，挂在本批第一条（用户）消息上。三者都不参与 messages() 的 LLM 历史。"""
        seq = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM conversation_messages WHERE conv_id = ?",
            (conv_id,)).fetchone()[0]
        last = len(msgs) - 1
        for i, m in enumerate(msgs):
            d = message_to_dict(m)
            steps_json = (json.dumps(steps, ensure_ascii=False)
                          if steps and i == last else None)
            progress_json = (json.dumps(progress, ensure_ascii=False)
                             if progress and i == last else None)
            attachments_json = (json.dumps(attachments, ensure_ascii=False)
                                if attachments and i == 0 else None)
            self._conn.execute(
                "INSERT INTO conversation_messages(conv_id, seq, role, content, tool_calls, "
                "tool_call_id, steps, progress, attachments, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (conv_id, seq, d["role"], d["content"],
                 json.dumps(d["tool_calls"], ensure_ascii=False) if d["tool_calls"] else None,
                 d["tool_call_id"], steps_json, progress_json, attachments_json, _now()))
            seq += 1
        self._conn.commit()

    def start_turn(self, conv_id: str, user_msg: Message, run_id: str,
                   attachments: list[dict] | None = None) -> None:
        """一轮开头：原子写入 user 消息 + 一条 streaming 占位 assistant（空内容，带 run_id），
        供刷新后前端识别「有一轮在进行、对应此 run」而发起接回。attachments 挂 user 那条。"""
        seq = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM conversation_messages WHERE conv_id = ?",
            (conv_id,)).fetchone()[0]
        ud = message_to_dict(user_msg)
        att_json = json.dumps(attachments, ensure_ascii=False) if attachments else None
        _cols = ("conv_id, seq, role, content, tool_calls, tool_call_id, steps, progress, "
                 "attachments, run_id, status, created_at")
        _ph = "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        self._conn.execute(
            f"INSERT INTO conversation_messages({_cols}) {_ph}",
            (conv_id, seq, ud["role"], ud["content"], None, None, None, None,
             att_json, run_id, "done", _now()))
        self._conn.execute(
            f"INSERT INTO conversation_messages({_cols}) {_ph}",
            (conv_id, seq + 1, "assistant", "", None, None, None, None,
             None, run_id, "streaming", _now()))
        self._conn.commit()

    def finish_turn(self, conv_id: str, run_id: str, content: str,
                    steps: list[dict] | None = None, progress: list[dict] | None = None,
                    status: str = "done", sources: list[dict] | None = None,
                    tokens: int | None = None, cost: float | None = None,
                    elapsed_ms: int | None = None, reasoning: str | None = None,
                    verify: dict | None = None) -> None:
        """一轮结束：按 run_id 把 streaming 占位 assistant UPDATE 为最终内容 + steps/progress/
        sources（参考来源）+ 状态 + 用量（tokens/cost）+ 耗时（elapsed_ms）+ reasoning（思考过程）
        + verify（交付门结构化判定轨迹，门未开时为 None）——刷新后仍能还原。"""
        self._conn.execute(
            "UPDATE conversation_messages SET content=?, steps=?, progress=?, sources=?, "
            "status=?, tokens=?, cost=?, elapsed_ms=?, reasoning=?, verify=? "
            "WHERE conv_id=? AND run_id=? AND role='assistant'",
            (content,
             json.dumps(steps, ensure_ascii=False) if steps else None,
             json.dumps(progress, ensure_ascii=False) if progress else None,
             json.dumps(sources, ensure_ascii=False) if sources else None,
             status, tokens, cost, elapsed_ms, reasoning or None,
             json.dumps(verify, ensure_ascii=False) if verify else None,
             conv_id, run_id))
        self._conn.commit()

    def flush_partial(self, conv_id: str, run_id: str, content: str) -> None:
        """生成中把已累积的部分文本写进 streaming 占位（不改 status/steps）——仅为服务重启后
        还能看到断点前的部分兜底；客户端刷新的主路径靠内存总线，不依赖它。"""
        self._conn.execute(
            "UPDATE conversation_messages SET content=? "
            "WHERE conv_id=? AND run_id=? AND role='assistant' AND status='streaming'",
            (content, conv_id, run_id))
        self._conn.commit()

    def reconcile_streaming(self) -> int:
        """启动对账：把残留的 streaming 助手消息标 interrupted（进程重启丢了在途后台任务）。"""
        cur = self._conn.execute(
            "UPDATE conversation_messages SET status='interrupted' "
            "WHERE role='assistant' AND status='streaming'")
        self._conn.commit()
        return cur.rowcount

    def conv_of_run(self, run_id: str) -> str | None:
        """run_id → 所属 conv_id（供 attach/stop 归属校验）。"""
        row = self._conn.execute(
            "SELECT conv_id FROM conversation_runs WHERE run_id = ?", (run_id,)).fetchone()
        return row[0] if row else None

    def ui_messages(self, conv_id: str) -> list[dict]:
        """供前端渲染：role + content + steps（工具调用轨迹）+ progress（沙箱/子代理进度）
        + attachments（用户上传附件元数据）+ run_id + status（续传用）。"""
        rows = self._conn.execute(
            "SELECT role, content, steps, progress, sources, attachments, run_id, status, "
            "tokens, cost, elapsed_ms, reasoning, verify "
            "FROM conversation_messages WHERE conv_id = ? ORDER BY seq", (conv_id,)).fetchall()
        return [{"role": role, "content": content,
                 "steps": json.loads(steps) if steps else None,
                 "progress": json.loads(progress) if progress else None,
                 "sources": json.loads(sources) if sources else None,
                 "attachments": json.loads(attachments) if attachments else None,
                 "run_id": run_id, "status": status,
                 "tokens": tokens, "cost": cost, "elapsed_ms": elapsed_ms,
                 "reasoning": reasoning,
                 "verify": json.loads(verify) if verify else None}
                for role, content, steps, progress, sources, attachments, run_id, status,
                tokens, cost, elapsed_ms, reasoning, verify in rows]

    def rename(self, user_id: str, conv_id: str, title: str) -> bool:
        cur = self._conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ? AND user_id = ?",
            (title, conv_id, user_id))
        self._conn.commit()
        return cur.rowcount > 0

    def get_title(self, user_id: str, conv_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT title FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id)).fetchone()
        return row[0] if row is not None else None

    def add_run(self, conv_id: str, run_id: str) -> None:
        """登记一次 Agent 运行归属于哪个会话，供删除会话时清理其检查点/轨迹。"""
        self._conn.execute(
            "INSERT OR IGNORE INTO conversation_runs(conv_id, run_id, created_at) "
            "VALUES (?, ?, ?)", (conv_id, run_id, _now()))
        self._conn.commit()

    def run_ids(self, user_id: str, conv_id: str) -> list[str]:
        """该会话的全部 run_id（带归属校验；会话不存在/非本人则返回空）。"""
        if not self.exists(user_id, conv_id):
            return []
        rows = self._conn.execute(
            "SELECT run_id FROM conversation_runs WHERE conv_id = ?", (conv_id,)).fetchall()
        return [r[0] for r in rows]

    def delete(self, user_id: str, conv_id: str) -> None:
        if not self.exists(user_id, conv_id):
            return
        self._conn.execute("DELETE FROM conversation_messages WHERE conv_id = ?", (conv_id,))
        self._conn.execute("DELETE FROM conversation_runs WHERE conv_id = ?", (conv_id,))
        self._conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        self._conn.commit()
