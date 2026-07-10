# app/api/mcp.py —— MCP 管理端点（配置驱动的动态增减）
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import current_user


def make_mcp_router(harness) -> APIRouter:
    """挂载条件：harness.mcp_manager 存在（即 enable_mcp=True）。"""
    router = APIRouter()

    @router.get("/api/mcp/servers")
    async def list_servers(user_id: str = Depends(current_user)):
        return {"servers": harness.mcp_manager.status()}

    @router.post("/api/mcp/reload")
    async def reload(user_id: str = Depends(current_user)):
        """重读 mcp_servers.json 并重连，把远程工具在全局 registry 里增删。

        因为请求期 _build_registry 每次全量复制全局 registry，reload 后下一条消息即生效。
        """
        old_names = harness.mcp_manager.tool_names()
        tools = await harness.mcp_manager.reload()
        for name in old_names:
            harness.registry.unregister(name)
        for t in tools:
            harness.registry.register(t)
        return {"servers": harness.mcp_manager.status(),
                "tools": [t.name for t in tools]}

    return router
