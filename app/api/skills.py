# app/api/skills.py —— 技能详情（前端展开「已启用技能」时按需取正文）
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user


def make_skills_router(harness) -> APIRouter:
    """挂载条件：harness.skill_registry 存在（配了 skills_dir 且目录非空）。

    正文按需取而不是随 Progress 事件下发：技能正文是**静态资源**（随部署固定，2-3KB/个），
    塞进事件就会连同 progress 列一起，在每条命中技能的助手消息里各存一份。
    """
    router = APIRouter()

    @router.get("/api/skills")
    async def list_skills(user_id: str = Depends(current_user)):
        reg = harness.skill_registry
        return {"skills": [{"name": n, "description": (reg.get(n).description if reg.get(n) else "")}
                           for n in reg.names()]}

    @router.get("/api/skills/{name}")
    async def get_skill(name: str, user_id: str = Depends(current_user)):
        skill = harness.skill_registry.get(name)
        if skill is None:
            # 历史消息里的技能可能已被删除或改名——前端据此提示，而不是干转圈
            raise HTTPException(status_code=404, detail="技能不存在")
        return {"name": skill.name, "description": skill.description, "body": skill.body}

    return router
