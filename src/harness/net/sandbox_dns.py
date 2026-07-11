# src/harness/net/sandbox_dns.py
from __future__ import annotations

# 在沙箱容器内解析主机名 → 打印去重后的 IP（每行一个）。抓取（HTTP/浏览器）真正出网都
# 发生在沙箱，故 DNS 也在沙箱内解析，避免宿主与沙箱两侧 DNS 视图不一致带来的 SSRF 绕过。
_RESOLVE_SCRIPT = (
    "import socket,sys\n"
    "print('\\n'.join(sorted({ai[4][0] for ai in socket.getaddrinfo(sys.argv[1], None)})))"
)


async def resolve_in_sandbox(sandbox, host: str, timeout: float = 30.0) -> list[str]:
    """在沙箱容器内解析 host，返回 IP 列表（供 net.policy.check_url 的 resolve 使用）。"""
    res = await sandbox.exec(["python3", "-c", _RESOLVE_SCRIPT, host], timeout + 5)
    return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
