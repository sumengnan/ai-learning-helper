from unittest.mock import Mock

from harness import progress
from harness.events import Progress
from harness.sandbox.docker import DockerSandbox


def test_emitter_set_and_reset():
    got = []
    token = progress.set_emitter(got.append)
    progress.emit(Progress("sandbox", "hi"))
    progress.reset_emitter(token)
    progress.emit(Progress("sandbox", "after"))   # 已 reset：无 emitter，忽略
    assert [(e.scope, e.text) for e in got] == [("sandbox", "hi")]


async def test_sandbox_start_emits_progress(monkeypatch):
    import docker
    import docker.tls

    def fake_client(base_url, tls):
        c = Mock()
        c.images.get.return_value = Mock()        # 镜像已存在，不走 pull 分支
        c.containers.run.return_value = Mock()
        return c

    monkeypatch.setattr(docker.tls, "TLSConfig", lambda **k: object())
    monkeypatch.setattr(docker, "DockerClient", fake_client)

    got = []
    token = progress.set_emitter(got.append)
    try:
        sb = DockerSandbox(docker_host="tcp://h:2376", image="python:3.12")
        await sb.start()
    finally:
        progress.reset_emitter(token)

    events = [e for e in got if isinstance(e, Progress)]
    assert {e.scope for e in events} == {"sandbox"}
    texts = [e.text for e in events]
    assert any("启动沙箱容器" in t for t in texts)
    assert any("就绪" in t for t in texts)


async def test_sandbox_exec_emits_progress_even_when_reused():
    # 容器已就绪（复用）：start() 提前返回不产进度，但每次 exec 仍应上报沙箱执行
    sb = DockerSandbox(docker_host="tcp://h:2376", image="python:3.12")
    container = Mock()
    exec_res = Mock()
    exec_res.output = (b"ok", b"")
    exec_res.exit_code = 0
    container.exec_run.return_value = exec_res
    sb._container = container

    got = []
    token = progress.set_emitter(got.append)
    try:
        await sb.exec(["sh", "-c", "ls"], timeout=5)
    finally:
        progress.reset_emitter(token)

    events = [e for e in got if isinstance(e, Progress)]
    assert events and all(e.scope == "sandbox" for e in events)
    texts = [e.text for e in events]
    assert any(t.startswith("执行 ") and t.endswith("…") for t in texts)
    assert "执行完成" in texts
