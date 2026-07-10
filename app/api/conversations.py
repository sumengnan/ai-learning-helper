# app/api/conversations.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user


class _Create(BaseModel):
    title: str | None = None


class _Rename(BaseModel):
    title: str


def make_conversations_router(store, harness=None, attachment_store=None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/conversations")
    async def list_conversations(user_id: str = Depends(current_user)):
        return store.list(user_id)

    @router.post("/api/conversations")
    async def create_conversation(body: _Create, user_id: str = Depends(current_user)):
        return {"id": store.create(user_id, body.title or "新对话")}

    @router.patch("/api/conversations/{conv_id}")
    async def rename_conversation(conv_id: str, body: _Rename,
                                  user_id: str = Depends(current_user)):
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="标题不能为空")
        if not store.rename(user_id, conv_id, title):
            raise HTTPException(status_code=404, detail="对话不存在")
        return {"ok": True}

    @router.get("/api/conversations/{conv_id}/messages")
    async def get_messages(conv_id: str, user_id: str = Depends(current_user)):
        if not store.exists(user_id, conv_id):
            raise HTTPException(status_code=404, detail="对话不存在")
        return store.ui_messages(conv_id)

    @router.delete("/api/conversations/{conv_id}")
    async def delete_conversation(conv_id: str, user_id: str = Depends(current_user)):
        # 1) 先取该会话的运行，清理 persistence 库里的检查点/轨迹（run_ids 带归属校验）
        run_ids = store.run_ids(user_id, conv_id)
        if harness is not None:
            ckpt = getattr(harness, "checkpoint_store", None)
            traj = getattr(harness, "trajectory_store", None)
            for rid in run_ids:
                if ckpt is not None:
                    ckpt.delete(rid)
                if traj is not None:
                    traj.delete(rid)
        # 2) 删会话 + 消息 + conversation_runs
        store.delete(user_id, conv_id)
        # 2.5) 清理该会话的上传附件（落盘文件 + 元数据行）
        if attachment_store is not None:
            attachment_store.delete_conv(conv_id)
        # 3) 销毁该会话的沙箱容器（含其中生成的临时文件）
        mgr = getattr(harness, "sandbox_manager", None) if harness is not None else None
        if mgr is not None:
            await mgr.destroy(conv_id)
        return {"ok": True}

    return router
