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
    uid = s.create("carol", "pw1234")
    assert s.get(uid) == {"id": uid, "username": "carol"}
    assert s.get("missing") is None


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
