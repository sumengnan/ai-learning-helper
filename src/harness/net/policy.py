# src/harness/net/policy.py
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class PolicyError(Exception):
    ...


def default_resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    return list({info[4][0] for info in infos})


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 无法识别的地址一律视为不安全
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _host_allowed(host: str, allowed_domains: list[str]) -> bool:
    host = host.lower()
    for d in allowed_domains:
        d = d.lower()
        if host == d or host.endswith("." + d):
            return True
    return False


def check_url(url: str, allowed_domains: list[str], block_private: bool,
              resolve=default_resolve) -> None:
    """URL 不合规则抛 PolicyError。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise PolicyError(f"仅允许 http/https：{parsed.scheme or '(空)'}")
    host = parsed.hostname
    if not host:
        raise PolicyError(f"无效 URL：{url}")
    if allowed_domains and not _host_allowed(host, allowed_domains):
        raise PolicyError(f"域名不在白名单：{host}")
    if block_private:
        ips = resolve(host)
        if not ips:
            raise PolicyError(f"无法解析主机：{host}")
        for ip in ips:
            if _is_blocked_ip(ip):
                raise PolicyError(f"目标为内网/保留地址，已拦截：{host} → {ip}")
