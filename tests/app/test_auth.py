import pytest

from app.auth import AuthService, UserStore, UsernameTaken


def _store():
    return UserStore(":memory:")


def test_create_and_verify():
    s = _store()
    uid = s.create("alice", "secret6")
    assert s.verify("alice", "secret6") == uid
    assert s.verify("alice", "wrong") is None
    assert s.verify("nobody", "secret6") is None


def test_duplicate_username_raises():
    s = _store()
    s.create("bob", "pw1234")
    with pytest.raises(UsernameTaken):
        s.create("bob", "other9")


def test_get_returns_public_fields():
    s = _store()
    uid = s.create("carol", "pw1234", "卡罗尔")
    assert s.get(uid) == {"id": uid, "username": "carol", "full_name": "卡罗尔"}
    assert s.get("missing") is None


def test_get_full_name_empty_for_legacy_accounts():
    """老账号没姓名（列为 NULL）→ 给空串而非 None，前端少一处判空。"""
    s = _store()
    uid = s.create("legacy", "pw1234")
    assert s.get(uid)["full_name"] == ""


def test_password_hash_is_not_plaintext():
    s = _store()
    s.create("dave", "plaintextpw")
    row = s._db.execute("SELECT password_hash FROM users WHERE username='dave'").fetchone()
    assert "plaintextpw" not in row[0]


def _auth(now=None):
    s = _store()
    kwargs = {"now": now} if now else {}
    return AuthService(s, secret="test-secret", **kwargs), s


def test_token_roundtrip():
    auth, s = _auth()
    uid = s.create("eve", "pw1234")
    tok = auth.issue_token(uid)
    got = auth.verify_token(tok)
    assert got is not None and got[0] == uid and got[1] > 0


def test_tampered_token_rejected():
    auth, s = _auth()
    tok = auth.issue_token(s.create("frank", "pw1234"))
    body, sig = tok.split(".")
    assert auth.verify_token(f"{body}x.{sig}") is None      # 篡改 payload
    assert auth.verify_token(f"{body}.{sig}x") is None       # 篡改签名
    assert auth.verify_token("garbage") is None
    assert auth.verify_token("") is None


def test_wrong_secret_rejected():
    a1, s = _auth()
    tok = a1.issue_token(s.create("grace", "pw1234"))
    a2 = AuthService(s, secret="other-secret")
    assert a2.verify_token(tok) is None


def test_expired_token_rejected():
    clock = {"t": 1000.0}
    auth = AuthService(UserStore(":memory:"), secret="s", ttl=100, now=lambda: clock["t"])
    tok = auth.issue_token("u1")
    clock["t"] = 1000.0 + 101          # 超过 ttl
    assert auth.verify_token(tok) is None


def test_near_expiry_remaining_under_renew_window():
    clock = {"t": 1000.0}
    auth = AuthService(UserStore(":memory:"), secret="s",
                       ttl=86_400, renew_within=3_600, now=lambda: clock["t"])
    tok = auth.issue_token("u1")
    clock["t"] = 1000.0 + 86_400 - 1_800      # 剩 30 分钟
    got = auth.verify_token(tok)
    assert got is not None and got[1] < auth.renew_within()


# ---- 姓名核身（忘记密码用）----

def test_verify_name_matches_and_normalizes():
    s = _store()
    uid = s.create("amy", "pw1234", "Li Ming")
    assert s.verify_name("amy", "  li   MING ") == uid   # 大小写/空白差异不该判失败
    assert s.verify_name("amy", "张三") is None
    assert s.verify_name("nobody", "Li Ming") is None


def test_verify_name_rejects_empty_on_both_sides():
    """老账号没姓名（NULL）+ 用户交空串 → 必须失败，不能「空配空」放行。

    API 层的 pydantic 会先把空姓名拦成 422，够不到这里；但那是另一层的约定，
    改一下校验就没了。这条守卫必须在 store 自己站得住。
    """
    s = _store()
    s.create("legacy", "pw1234")            # 不传姓名，模拟老库账号
    assert s.verify_name("legacy", "") is None
    assert s.verify_name("legacy", "   ") is None
    assert s.verify_name("legacy", "随便写") is None
    # 反向：有姓名的账号也不能被空输入匹配上
    s.create("amy", "pw1234", "艾米")
    assert s.verify_name("amy", "") is None


def test_set_password_changes_hash_and_salt():
    s = _store()
    uid = s.create("amy", "old1234", "艾米")
    old = s._db.execute("SELECT password_hash, salt FROM users WHERE id=?", (uid,)).fetchone()
    assert s.set_password(uid, "new1234") is True
    assert s.verify("amy", "new1234") == uid
    assert s.verify("amy", "old1234") is None
    new = s._db.execute("SELECT password_hash, salt FROM users WHERE id=?", (uid,)).fetchone()
    # salt 一并换掉：旧 salt 可能已随旧库泄露，重置正是换掉它的时机
    assert new[1] != old[1]


def test_set_password_unknown_user_returns_false():
    assert _store().set_password("nobody", "new1234") is False


def test_set_full_name_lets_legacy_account_use_reset():
    s = _store()
    uid = s.create("legacy", "pw1234")
    assert s.verify_name("legacy", "老王") is None       # 补填前用不了
    assert s.set_full_name(uid, "老王") is True
    assert s.verify_name("legacy", "老王") == uid
    assert s.set_full_name(uid, "   ") is False          # 空白不算填
