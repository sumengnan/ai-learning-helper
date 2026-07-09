import os
from unittest.mock import Mock

import pytest

from harness.config import HarnessConfig
from harness.sandbox.base import SandboxError
from harness.sandbox.docker import DockerSandbox
from harness.sandbox.factory import build_sandbox
from harness.sandbox.local import LocalSandbox


def _mock_docker_sandbox():
    sb = DockerSandbox(docker_host="tcp://h:2376", image="python:3.12-slim")
    container = Mock()
    exec_res = Mock()
    exec_res.output = (b"", b"")
    exec_res.exit_code = 0
    container.exec_run.return_value = exec_res
    sb._container = container
    sb._client = Mock()
    return sb, container


def test_factory_local_default():
    cfg = HarnessConfig(api_key="k", _env_file=None)   # 断言源码默认，不受本地 .env 影响
    assert isinstance(build_sandbox(cfg), LocalSandbox)


async def test_start_builds_tls_client(monkeypatch):
    # 直连 daemon TLS 端口：start() 应以配置的证书构造 TLSConfig 并传给 DockerClient
    import docker
    import docker.tls
    captured = {}

    class FakeTLS:
        def __init__(self, **kw):
            captured["tls_kwargs"] = kw

    def fake_docker_client(base_url, tls):
        captured["base_url"] = base_url
        captured["tls"] = tls
        client = Mock()
        client.containers.run.return_value = Mock()
        return client

    monkeypatch.setattr(docker.tls, "TLSConfig", FakeTLS)
    monkeypatch.setattr(docker, "DockerClient", fake_docker_client)

    sb = DockerSandbox(
        docker_host="tcp://h:2376", image="python:3.12-slim",
        tls_ca_cert="/certs/ca.pem", tls_client_cert="/certs/cert.pem",
        tls_client_key="/certs/key.pem", tls_verify=True)
    await sb.start()

    assert captured["base_url"] == "tcp://h:2376"
    assert isinstance(captured["tls"], FakeTLS)
    assert captured["tls_kwargs"]["client_cert"] == ("/certs/cert.pem", "/certs/key.pem")
    assert captured["tls_kwargs"]["ca_cert"] == "/certs/ca.pem"
    assert captured["tls_kwargs"]["verify"] is True


async def test_list_files_enforces_path_constraint():
    # #2：list_files 必须先 resolve_in_workspace，逃逸路径抛 SandboxError
    sb, container = _mock_docker_sandbox()
    with pytest.raises(SandboxError):
        await sb.list_files("/etc")
    with pytest.raises(SandboxError):
        await sb.list_files("../..")
    container.exec_run.assert_not_called()


async def test_exec_timeout_ceil_not_truncated():
    # #6：timeout<1 不应被 int() 截成 0（=不限时），应向上取整为至少 1
    sb, container = _mock_docker_sandbox()
    await sb.exec(["echo", "hi"], timeout=0.5)
    wrapped = container.exec_run.call_args.args[0]
    assert wrapped[:2] == ["timeout", "1"]


async def test_close_still_closes_client_on_remove_error():
    # #8：remove 抛异常时仍要 client.close()，且两者状态都置空
    sb, container = _mock_docker_sandbox()
    client = sb._client
    container.remove.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await sb.close()
    client.close.assert_called_once()
    assert sb._container is None
    assert sb._client is None


@pytest.mark.skipif(not os.getenv("HARNESS_SANDBOX_DOCKER_HOST"),
                    reason="需要真实远程 docker（HARNESS_SANDBOX_DOCKER_HOST）")
async def test_docker_exec_roundtrip():
    cfg = HarnessConfig(
        api_key="k", sandbox_backend="docker",
        sandbox_docker_host=os.environ["HARNESS_SANDBOX_DOCKER_HOST"],
        sandbox_docker_tls_ca_cert=os.environ.get("HARNESS_SANDBOX_DOCKER_TLS_CA_CERT", ""),
        sandbox_docker_tls_client_cert=os.environ.get("HARNESS_SANDBOX_DOCKER_TLS_CLIENT_CERT", ""),
        sandbox_docker_tls_client_key=os.environ.get("HARNESS_SANDBOX_DOCKER_TLS_CLIENT_KEY", ""))
    sb = build_sandbox(cfg)
    await sb.start()
    try:
        await sb.write_file("a.txt", "hi")
        assert await sb.read_file("a.txt") == "hi"
        r = await sb.exec(["python3", "-c", "print(6*7)"], timeout=15)
        assert "42" in r.stdout
    finally:
        await sb.close()
