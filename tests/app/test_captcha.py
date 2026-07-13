import random

from app import captcha


def test_sign_verify_roundtrip():
    secret = b"s3cr3t"
    token = captcha.sign(secret, "AbCd")
    # 大小写不敏感、忽略首尾空格
    assert captcha.verify(secret, token, "abcd")
    assert captcha.verify(secret, token, "  ABCD ")
    assert not captcha.verify(secret, token, "abce")
    assert not captcha.verify(secret, token, "")


def test_verify_rejects_tampered_or_malformed():
    secret = b"s3cr3t"
    token = captcha.sign(secret, "WXYZ")
    body, sig = token.split(".")
    assert not captcha.verify(secret, f"{body}.deadbeef", "wxyz")   # 签名被改
    assert not captcha.verify(secret, "no-dot-token", "wxyz")       # 结构非法
    assert not captcha.verify(b"other-secret", token, "wxyz")       # 密钥不符


def test_verify_rejects_expired():
    secret = b"s3cr3t"
    clock = [1000]
    token = captcha.sign(secret, "PASS", ttl=60, now=lambda: clock[0])
    clock[0] = 1000 + 61  # 过期
    assert not captcha.verify(secret, token, "pass", now=lambda: clock[0])


def test_random_code_charset_and_length():
    for _ in range(50):
        code = captcha.random_code(4)
        assert len(code) == 4
        assert not any(c in "0O1Il" for c in code)


def test_render_svg_contains_chars_and_is_svg():
    svg = captcha.render_svg("Ab7", rng=random.Random(0))
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    for ch in "Ab7":
        assert f">{ch}</text>" in svg
    assert captcha.data_uri(svg).startswith("data:image/svg+xml;base64,")
