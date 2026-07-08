import pytest
from harness.net.policy import check_url, PolicyError


def _to(ip):
    return lambda host: [ip]


def test_block_loopback():
    with pytest.raises(PolicyError):
        check_url("http://x/", [], True, resolve=_to("127.0.0.1"))


def test_block_metadata():
    with pytest.raises(PolicyError):
        check_url("http://x/", [], True, resolve=_to("169.254.169.254"))


def test_block_private():
    with pytest.raises(PolicyError):
        check_url("http://x/", [], True, resolve=_to("10.0.0.5"))


def test_public_allowed():
    check_url("http://x/", [], True, resolve=_to("93.184.216.34"))  # 不抛


def test_allowlist_reject_and_accept():
    with pytest.raises(PolicyError):
        check_url("http://evil.com/", ["example.com"], False, resolve=_to("1.2.3.4"))
    check_url("http://api.example.com/", ["example.com"], False, resolve=_to("1.2.3.4"))


def test_scheme_rejected():
    with pytest.raises(PolicyError):
        check_url("file:///etc/passwd", [], True)
