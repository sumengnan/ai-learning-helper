# app/api/site_info.py
from __future__ import annotations

from fastapi import APIRouter


def make_site_router(config) -> APIRouter:
    """暴露站点备案信息（ICP / 公安备案 / 主体名称），供登录、注册页页脚展示。

    走后端配置而非前端常量：备案号随部署主体变（换主体、多实例分别备案），
    改 .env 重启即可，不必重新构建前端产物。
    """
    router = APIRouter()

    # 不挂鉴权：备案信息按规定要在未登录的公开页面上展示，登录页取不到就等于没展示。
    # 三项均为公开信息，不涉敏感。
    @router.get("/api/site")
    async def site() -> dict:
        def _s(name: str) -> str:
            return str(getattr(config, name, "") or "").strip()

        return {
            "icp": _s("site_icp"),
            "police_icp": _s("site_police_icp"),
            "copyright": _s("site_copyright"),
        }

    return router
