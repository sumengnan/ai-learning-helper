import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.assembly import Harness
from app.config import AppConfig
from app.conversations import ConversationStore
from app.documents import DocumentStore
from app.main import create_app
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool


@pytest.fixture(autouse=True)
def _sqlite_allow_cross_thread(monkeypatch):
    original = sqlite3.connect

    def _patched(*args, **kwargs):
        kwargs.setdefault("check_same_thread", False)
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _patched)


def _client(require_captcha=False):
    reg = ToolRegistry(); reg.register(CalculatorTool())
    traj = TrajectoryStore(":memory:")
    harness = Harness(client=None, registry=reg,
                      checkpoint_store=CheckpointStore(":memory:"),
                      trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="s")
    cfg = AppConfig(api_key="k", app_db_path=":memory:", require_captcha=require_captcha)
    app = create_app(config=cfg, harness=harness, store=ConversationStore(":memory:"),
                     doc_store=DocumentStore(":memory:"))
    return TestClient(app)


def _register(client, username, password="pw1234"):
    r = client.post("/api/auth/register", json={"username": username, "full_name": "测试用户", "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_returns_token_and_user():
    client = _client()
    r = client.post("/api/auth/register", json={"username": "alice", "full_name": "测试用户", "password": "pw1234"})
    assert r.status_code == 200
    body = r.json()
    assert body["token"] and body["user"]["username"] == "alice"


def test_register_duplicate_400():
    client = _client()
    _register(client, "bob")
    r = client.post("/api/auth/register", json={"username": "bob", "full_name": "测试用户", "password": "pw1234"})
    assert r.status_code == 400


def test_register_short_password_422():
    client = _client()
    r = client.post("/api/auth/register", json={"username": "x", "full_name": "测试用户", "password": "123"})
    assert r.status_code == 422


def test_register_blank_username_422():
    client = _client()
    r = client.post("/api/auth/register", json={"username": "  ", "full_name": "测试用户", "password": "pw1234"})
    assert r.status_code == 422


def test_login_ok_and_wrong_password():
    client = _client()
    _register(client, "carol")
    assert client.post("/api/auth/login",
                       json={"username": "carol", "password": "pw1234"}).status_code == 200
    r = client.post("/api/auth/login", json={"username": "carol", "password": "nope99"})
    assert r.status_code == 401


def test_me_requires_token():
    client = _client()
    token = _register(client, "dave")
    assert client.get("/api/auth/me").status_code == 401
    r = client.get("/api/auth/me", headers=_hdr(token))
    assert r.status_code == 200 and r.json()["username"] == "dave"


def test_protected_route_without_token_401():
    client = _client()
    assert client.get("/api/conversations").status_code == 401
    assert client.post("/api/conversations", json={}).status_code == 401


def test_bad_token_401():
    client = _client()
    assert client.get("/api/conversations", headers=_hdr("garbage.sig")).status_code == 401


def test_data_isolation_between_users():
    client = _client()
    ta = _register(client, "userA")
    tb = _register(client, "userB")
    cid = client.post("/api/conversations", json={"title": "A的对话"}, headers=_hdr(ta)).json()["id"]

    # A 能看到，B 看不到
    assert any(c["id"] == cid for c in client.get("/api/conversations", headers=_hdr(ta)).json())
    assert client.get("/api/conversations", headers=_hdr(tb)).json() == []

    # B 无法读取 A 的消息，也无法删除
    assert client.get(f"/api/conversations/{cid}/messages", headers=_hdr(tb)).status_code == 404
    client.delete(f"/api/conversations/{cid}", headers=_hdr(tb))
    assert client.get(f"/api/conversations/{cid}/messages", headers=_hdr(ta)).status_code == 200


def test_rename_conversation():
    client = _client()
    ta = _register(client, "renamer")
    cid = client.post("/api/conversations", json={}, headers=_hdr(ta)).json()["id"]
    assert client.patch(f"/api/conversations/{cid}",
                        json={"title": "新名字"}, headers=_hdr(ta)).status_code == 200
    got = client.get("/api/conversations", headers=_hdr(ta)).json()
    assert got[0]["title"] == "新名字"
    # 改别人的（不存在的）→ 404
    assert client.patch("/api/conversations/nope",
                        json={"title": "x"}, headers=_hdr(ta)).status_code == 404


def _solve_captcha(client):
    """从 app.state.auth 直接签一枚验证码，返回可直接提交的 (token, code)。"""
    return client.app.state.auth.issue_captcha()


def test_captcha_endpoint_returns_token_and_image():
    client = _client(require_captcha=True)
    r = client.get("/api/auth/captcha")
    assert r.status_code == 200
    body = r.json()
    assert body["token"].count(".") == 1
    assert body["image"].startswith("data:image/svg+xml;base64,")


def test_register_requires_valid_captcha_when_enabled():
    client = _client(require_captcha=True)
    # 缺验证码 → 400
    r = client.post("/api/auth/register", json={"username": "cap1", "full_name": "测试用户", "password": "pw1234"})
    assert r.status_code == 400
    # 错验证码 → 400
    token, _code = _solve_captcha(client)
    r = client.post("/api/auth/register", json={
        "username": "cap1", "full_name": "测试用户", "password": "pw1234",
        "captcha_token": token, "captcha_text": "zzzz"})
    assert r.status_code == 400
    # 正确验证码 → 200
    token, code = _solve_captcha(client)
    r = client.post("/api/auth/register", json={
        "username": "cap1", "full_name": "测试用户", "password": "pw1234",
        "captcha_token": token, "captcha_text": code})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["username"] == "cap1"


def test_login_requires_valid_captcha_when_enabled():
    client = _client(require_captcha=True)
    token, code = _solve_captcha(client)
    client.post("/api/auth/register", json={
        "username": "cap2", "full_name": "测试用户", "password": "pw1234",
        "captcha_token": token, "captcha_text": code})
    # 密码对但验证码缺失 → 400（验证码先于凭据校验）
    r = client.post("/api/auth/login", json={"username": "cap2", "password": "pw1234"})
    assert r.status_code == 400
    # 验证码对 → 200
    token, code = _solve_captcha(client)
    r = client.post("/api/auth/login", json={
        "username": "cap2", "password": "pw1234",
        "captcha_token": token, "captcha_text": code})
    assert r.status_code == 200, r.text


def test_captcha_ignored_when_disabled():
    # 默认关：不带验证码也能注册/登录（保持既有行为）
    client = _client()
    assert client.post("/api/auth/register",
                       json={"username": "nocap", "full_name": "测试用户", "password": "pw1234"}).status_code == 200


def test_refresh_header_on_near_expiry(monkeypatch):
    # 直接构造快过期 token，验证 current_user 下发 X-Refresh-Token
    client = _client()
    token = _register(client, "renewer")
    auth = client.app.state.auth
    uid = auth.verify_token(token)[0]
    # 用短 ttl 的服务签发一个"剩余 < renew_within"的 token
    from app.auth import AuthService
    near = AuthService(auth.users, secret=auth._secret.decode(),
                       ttl=auth.renew_within() - 60)
    short_tok = near.issue_token(uid)
    r = client.get("/api/auth/me", headers=_hdr(short_tok))
    assert r.status_code == 200
    assert "X-Refresh-Token" in r.headers


# ---- 姓名字段与忘记密码 ----

def _reset(client, username, full_name, new_password="new1234", **kw):
    return client.post("/api/auth/reset-password", json={
        "username": username, "full_name": full_name,
        "new_password": new_password, **kw})


def test_register_requires_full_name():
    """姓名是找回密码的唯一凭据，留空的账号日后无从自助重置，故注册时必填。"""
    client = _client()
    r = client.post("/api/auth/register",
                    json={"username": "noname", "password": "pw1234"})
    assert r.status_code == 422
    assert "姓名" in r.json()["detail"]
    # 只有空白也不算填
    assert client.post("/api/auth/register", json={
        "username": "noname", "full_name": "   ", "password": "pw1234"}).status_code == 422


def test_reset_password_with_matching_name():
    client = _client()
    client.post("/api/auth/register",
                json={"username": "amy", "full_name": "艾米", "password": "old1234"})
    assert _reset(client, "amy", "艾米").status_code == 200
    # 新密码可登录，旧密码失效
    assert client.post("/api/auth/login",
                       json={"username": "amy", "password": "new1234"}).status_code == 200
    assert client.post("/api/auth/login",
                       json={"username": "amy", "password": "old1234"}).status_code == 401


def test_reset_does_not_log_user_in():
    """重置成功刻意不签发 token：猜对姓名不该直接变成一次静默的账号接管。"""
    client = _client()
    client.post("/api/auth/register",
                json={"username": "amy", "full_name": "艾米", "password": "old1234"})
    assert "token" not in _reset(client, "amy", "艾米").json()


def test_reset_name_match_ignores_case_and_spacing():
    """「Li Ming」和「li  ming 」是同一个人；这种差异判失败只会把用户挡在自己账号外。"""
    client = _client()
    client.post("/api/auth/register",
                json={"username": "lm", "full_name": "Li Ming", "password": "old1234"})
    assert _reset(client, "lm", "  li   MING ").status_code == 200


def test_reset_wrong_name_rejected_and_password_unchanged():
    client = _client()
    client.post("/api/auth/register",
                json={"username": "amy", "full_name": "艾米", "password": "old1234"})
    assert _reset(client, "amy", "张三").status_code == 400
    # 密码没被改动
    assert client.post("/api/auth/login",
                       json={"username": "amy", "password": "old1234"}).status_code == 200


def test_reset_does_not_leak_account_existence():
    """账号不存在与姓名不对必须同一句话——分开说等于白送一个账号枚举接口。"""
    client = _client()
    client.post("/api/auth/register",
                json={"username": "amy", "full_name": "艾米", "password": "old1234"})
    missing = _reset(client, "nobody", "艾米")
    wrong = _reset(client, "amy", "张三")
    assert missing.status_code == wrong.status_code == 400
    assert missing.json()["detail"] == wrong.json()["detail"]


def test_reset_rejects_legacy_account_without_name():
    """老账号 full_name 为 NULL：不能让空输入配上空姓名，须失败关闭。"""
    client = _client()
    app = client.app
    app.state.auth.users.create("legacy", "old1234")     # 不带姓名，模拟老库
    assert _reset(client, "legacy", "").status_code == 422       # 前置校验先拦
    assert _reset(client, "legacy", "随便写").status_code == 400


def test_reset_enforces_min_password():
    client = _client()
    client.post("/api/auth/register",
                json={"username": "amy", "full_name": "艾米", "password": "old1234"})
    assert _reset(client, "amy", "艾米", new_password="123").status_code == 422


def test_reset_throttled_after_repeated_failures():
    """姓名是弱凭据，验证码挡不住有人对着一个已知账号慢慢试——失败到上限即冷却。"""
    client = _client()
    client.post("/api/auth/register",
                json={"username": "amy", "full_name": "艾米", "password": "old1234"})
    for _ in range(5):
        assert _reset(client, "amy", "猜错的名字").status_code == 400
    r = _reset(client, "amy", "猜错的名字")
    assert r.status_code == 429
    # 锁定期内，即便姓名猜对了也不放行
    assert _reset(client, "amy", "艾米").status_code == 429


def test_throttle_is_per_account():
    """按账号计：不该因为别人被锁而连累本账号。"""
    client = _client()
    for name in ("amy", "bob"):
        client.post("/api/auth/register",
                    json={"username": name, "full_name": "艾米", "password": "old1234"})
    for _ in range(6):
        _reset(client, "amy", "猜错的名字")
    assert _reset(client, "bob", "艾米").status_code == 200


def test_throttle_cleared_after_success():
    client = _client()
    client.post("/api/auth/register",
                json={"username": "amy", "full_name": "艾米", "password": "old1234"})
    for _ in range(4):
        _reset(client, "amy", "猜错的名字")
    assert _reset(client, "amy", "艾米").status_code == 200
    # 成功即清零：不该只剩 1 次机会
    for _ in range(4):
        assert _reset(client, "amy", "又猜错了").status_code == 400


def test_reset_requires_captcha_when_enabled():
    client = _client(require_captcha=True)
    token, code = _solve_captcha(client)
    client.post("/api/auth/register", json={
        "username": "amy", "full_name": "艾米", "password": "old1234",
        "captcha_token": token, "captcha_text": code})
    assert _reset(client, "amy", "艾米").status_code == 400          # 缺验证码
    token, code = _solve_captcha(client)
    assert _reset(client, "amy", "艾米",
                  captcha_token=token, captcha_text=code).status_code == 200
