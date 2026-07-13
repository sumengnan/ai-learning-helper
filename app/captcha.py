# app/captcha.py
"""无状态图形验证码：与鉴权 token 同源思路——用 HMAC 把答案签进 token，
服务端不落库即可校验。

token = base64url(json({exp, n})).base64url(hmac_sha256(secret, code_lower + "|" + body))

签发时 code 只进签名、不进明文；校验时用用户输入重算签名比对。故：
- 篡改 token 或改答案都会签名不符；
- exp 到期即失效（默认 5 分钟）；
- nonce 让同一验证码每次签发的 token 各不相同（避免图片-token 可预测复用）。

局限：TTL 窗口内同一 token 可重复提交（无一次性存储）。对本项目足够——
每次进入登录/注册页都会换新验证码，窗口短。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import secrets
import time

# 剔除易混淆字符（0/O、1/I/l 等），与前端展示保持一致的字符集
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYabcdefghjkmnpqrstuvwxy23456789"
DEFAULT_TTL = 300  # 秒

_WIDTH = 120
_HEIGHT = 44
_LIGHT_PALETTE = ["#4f46e5", "#7c3aed", "#2563eb", "#db2777", "#059669"]


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def random_code(n: int = 4) -> str:
    """生成 n 位随机验证码（密码学安全随机源）。"""
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def _normalize(text: str) -> str:
    return text.strip().lower()


def sign(secret: bytes, code: str, *, ttl: int = DEFAULT_TTL, now=time.time) -> str:
    """把 code 签进一个带 exp 的无状态 token。"""
    exp = int(now()) + ttl
    payload = {"exp": exp, "n": secrets.token_hex(6)}
    body = _b64u(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    msg = f"{_normalize(code)}|{body}".encode("utf-8")
    sig = _b64u(hmac.new(secret, msg, hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify(secret: bytes, token: str, text: str, *, now=time.time) -> bool:
    """校验用户输入 text 是否匹配 token 签进的验证码，且未过期。"""
    if not token or not text or token.count(".") != 1:
        return False
    body, sig = token.split(".")
    try:
        payload = json.loads(_b64u_decode(body))
    except (ValueError, TypeError):
        return False
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp - int(now()) <= 0:
        return False
    expected = _b64u(hmac.new(secret, f"{_normalize(text)}|{body}".encode("utf-8"),
                              hashlib.sha256).digest())
    return hmac.compare_digest(sig, expected)


def render_svg(code: str, *, rng: random.Random | None = None) -> str:
    """把验证码渲染成带干扰的 SVG（逐字随机旋转/位移/配色 + 噪点/干扰线）。"""
    r = rng or random.Random()
    palette = _LIGHT_PALETTE
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}" height="{_HEIGHT}" '
        f'viewBox="0 0 {_WIDTH} {_HEIGHT}" role="img" aria-label="图形验证码">',
        f'<rect width="{_WIDTH}" height="{_HEIGHT}" fill="#f1f3f8"/>',
    ]
    # 干扰线
    for _ in range(4):
        c = r.choice(palette)
        parts.append(
            f'<line x1="{r.randint(0, _WIDTH)}" y1="{r.randint(0, _HEIGHT)}" '
            f'x2="{r.randint(0, _WIDTH)}" y2="{r.randint(0, _HEIGHT)}" '
            f'stroke="{c}" stroke-width="1" opacity="0.35"/>')
    # 噪点
    for _ in range(24):
        c = r.choice(palette)
        parts.append(
            f'<circle cx="{r.randint(0, _WIDTH)}" cy="{r.randint(0, _HEIGHT)}" '
            f'r="{r.choice([0.8, 1.0, 1.4])}" fill="{c}" opacity="0.5"/>')
    # 字符
    step = _WIDTH / (len(code) + 1)
    for i, ch in enumerate(code):
        x = step * (i + 1)
        y = _HEIGHT / 2 + r.uniform(-3, 3)
        rot = r.uniform(-24, 24)
        size = r.randint(24, 29)
        c = r.choice(palette)
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="PingFang SC, system-ui, sans-serif" '
            f'font-size="{size}" font-weight="700" fill="{c}" '
            f'text-anchor="middle" dominant-baseline="middle" '
            f'transform="rotate({rot:.1f} {x:.1f} {y:.1f})">{ch}</text>')
    parts.append("</svg>")
    return "".join(parts)


def data_uri(svg: str) -> str:
    """SVG → data:URI（base64），供前端 <img src> 直接使用。"""
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
