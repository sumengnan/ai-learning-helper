# tests/app/test_site_info_api.py
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.site_info import make_site_router


def _client(**kw) -> TestClient:
    cfg = SimpleNamespace(**{"site_icp": "", "site_police_icp": "", "site_copyright": "", **kw})
    app = FastAPI()
    app.include_router(make_site_router(cfg))
    return TestClient(app)


def test_site_info_returns_configured_values():
    body = _client(site_icp="京ICP备12345678号-1",
                   site_police_icp="京公网安备 11010102000001号",
                   site_copyright="某某科技有限公司").get("/api/site").json()
    assert body == {"icp": "京ICP备12345678号-1",
                    "police_icp": "京公网安备 11010102000001号",
                    "copyright": "某某科技有限公司"}


def test_site_info_is_public_no_auth_required():
    # 登录页要在鉴权前就能读到，故此端点不挂 current_user 依赖
    assert _client(site_icp="沪ICP备1号").get("/api/site").status_code == 200


def test_site_info_all_empty_when_unconfigured():
    # 未配则各字段为空串，前端据此整块不渲染
    assert _client().get("/api/site").json() == {"icp": "", "police_icp": "", "copyright": ""}


def test_site_info_strips_whitespace():
    # .env 里手抖多打的空格不该渗进页面，也不该让"空配置"被误判成已配置
    body = _client(site_icp="  京ICP备1号  ", site_copyright="   ").get("/api/site").json()
    assert body["icp"] == "京ICP备1号"
    assert body["copyright"] == ""
