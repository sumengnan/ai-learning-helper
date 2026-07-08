import os
import pytest

from harness.config import HarnessConfig
from harness.sandbox.factory import build_sandbox
from harness.sandbox.local import LocalSandbox


def test_factory_local_default():
    cfg = HarnessConfig(api_key="k")
    assert isinstance(build_sandbox(cfg), LocalSandbox)


@pytest.mark.skipif(not os.getenv("HARNESS_SANDBOX_DOCKER_HOST"),
                    reason="需要真实远程 docker（HARNESS_SANDBOX_DOCKER_HOST）")
async def test_docker_exec_roundtrip():
    cfg = HarnessConfig(api_key="k", sandbox_backend="docker",
                        sandbox_docker_host=os.environ["HARNESS_SANDBOX_DOCKER_HOST"])
    sb = build_sandbox(cfg)
    await sb.start()
    try:
        await sb.write_file("a.txt", "hi")
        assert await sb.read_file("a.txt") == "hi"
        r = await sb.exec(["python3", "-c", "print(6*7)"], timeout=15)
        assert "42" in r.stdout
    finally:
        await sb.close()
