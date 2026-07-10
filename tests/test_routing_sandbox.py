import pytest

from harness.sandbox.base import ExecResult
from harness.sandbox.routing import RoutingSandbox


class _StubSandbox:
    def __init__(self, image):
        self.image = image
        self.workspace = "/workspace"
        self.started = False
        self.closed = False
        self.execs: list[list[str]] = []
        self.writes: list[tuple[str, str]] = []

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    async def exec(self, command, timeout):
        self.execs.append(command)
        return ExecResult("", "", 0)

    async def write_file(self, path, content):
        self.writes.append((path, content))

    async def read_file(self, path):
        return ""

    async def list_files(self, path="."):
        return []


def _make():
    made: dict[str, _StubSandbox] = {}
    images = {"python": "py:img", "node": "node:img", "java": "java:img"}

    def factory(lang):
        box = _StubSandbox(images[lang])
        made[lang] = box
        return box

    rs = RoutingSandbox(images=images, default_language="python", factory=factory)
    return rs, made


async def test_default_container_created_eagerly_but_not_started():
    rs, made = _make()
    assert "python" in made and made["python"].started is False
    assert rs.workspace == "/workspace"


async def test_sandbox_for_starts_only_that_language():
    rs, made = _make()
    box = await rs.sandbox_for("node")
    assert box is made["node"] and box.started is True
    assert "java" not in made                      # 未用语言不创建
    assert made["python"].started is False          # 默认容器未被 node 调用带起


async def test_protocol_methods_hit_default_container():
    rs, made = _make()
    await rs.exec(["ls"], timeout=5)
    await rs.write_file("a.txt", "x")
    assert made["python"].execs == [["ls"]]
    assert made["python"].writes == [("a.txt", "x")]
    assert "node" not in made


async def test_unknown_language_falls_back_to_default():
    rs, made = _make()
    box = await rs.sandbox_for("rust")
    assert box is made["python"]


async def test_close_closes_all_created_boxes():
    rs, made = _make()
    await rs.sandbox_for("node")
    await rs.sandbox_for("java")
    await rs.close()
    assert all(b.closed for b in made.values())


def test_empty_images_rejected():
    with pytest.raises(ValueError):
        RoutingSandbox(images={}, default_language="python", factory=lambda l: None)
