# src/harness/sandbox/docker.py
from __future__ import annotations

import asyncio
import io
import math
import os
import tarfile

from .base import ExecResult, resolve_in_workspace


class DockerSandbox:
    """远程 Linux 云服务器的 Docker 容器沙箱（docker SDK 直连 daemon 的 TLS 端口）。真正的安全边界。

    已知限制（容器内符号链接）：resolve_in_workspace 的 os.path.realpath 符号链接
    检测运行在宿主机侧，对容器内的路径无效；因此 /workspace 的路径限制是尽力而为的
    （能拦住 ../、绝对路径这类显式逃逸）。真正的安全边界是容器隔离本身——read_only
    根文件系统 + cap_drop=ALL + 非 root 用户 + network=none + 用后一次性销毁；即便在
    容器内借符号链接逃出 /workspace，也只能读到同一个一次性容器内的文件。
    """

    def __init__(self, docker_host: str, image: str, workspace: str = "/workspace",
                 user: str = "1000:1000", network: str = "none", mem_limit: str = "512m",
                 cpus: float = 1.0, pids_limit: int = 128, read_only: bool = False,
                 tls_ca_cert: str = "", tls_client_cert: str = "",
                 tls_client_key: str = "", tls_verify: bool = True) -> None:
        self.workspace = workspace
        self._docker_host = docker_host
        self._image = image
        self._user = user
        self._network = network
        self._mem_limit = mem_limit
        self._cpus = cpus
        self._pids_limit = pids_limit
        self._read_only = read_only
        self._tls_ca_cert = tls_ca_cert
        self._tls_client_cert = tls_client_cert
        self._tls_client_key = tls_client_key
        self._tls_verify = tls_verify
        self._client = None
        self._container = None

    def _tls_config(self):
        """按配置构造 docker TLS 客户端配置：双向 TLS（客户端证书/私钥 + CA）。"""
        from docker.tls import TLSConfig
        client_cert = ((self._tls_client_cert, self._tls_client_key)
                       if self._tls_client_cert and self._tls_client_key else None)
        return TLSConfig(
            client_cert=client_cert,
            ca_cert=self._tls_ca_cert or None,
            verify=self._tls_verify,
        )

    async def start(self) -> None:
        if self._container is not None:
            return
        import docker
        self._client = await asyncio.to_thread(
            docker.DockerClient, base_url=self._docker_host, tls=self._tls_config())
        self._container = await asyncio.to_thread(
            self._client.containers.run,
            self._image, command="sleep infinity", detach=True,
            working_dir=self.workspace, user=self._user, network_mode=self._network,
            read_only=self._read_only, tmpfs={self.workspace: "rw,size=64m"},
            mem_limit=self._mem_limit, nano_cpus=int(self._cpus * 1e9),
            pids_limit=self._pids_limit, cap_drop=["ALL"],
            security_opt=["no-new-privileges"], auto_remove=False)

    async def close(self) -> None:
        try:
            if self._container is not None:
                await asyncio.to_thread(self._container.remove, force=True)
        finally:
            self._container = None
            if self._client is not None:
                await asyncio.to_thread(self._client.close)
                self._client = None

    async def exec(self, command: list[str], timeout: float) -> ExecResult:
        await self.start()
        wrapped = ["timeout", str(max(1, math.ceil(timeout))), *command]
        res = await asyncio.to_thread(
            self._container.exec_run, wrapped, workdir=self.workspace, demux=True)
        out, err = res.output if isinstance(res.output, tuple) else (res.output, b"")
        return ExecResult((out or b"").decode(errors="replace"),
                          (err or b"").decode(errors="replace"),
                          res.exit_code, timed_out=(res.exit_code == 124))

    async def write_file(self, path: str, content: str) -> None:
        await self.start()
        real = resolve_in_workspace(self.workspace, path)
        data = content.encode()
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as tar:
            info = tarfile.TarInfo(name=os.path.basename(real))
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        await asyncio.to_thread(
            self._container.put_archive, os.path.dirname(real), stream.getvalue())

    async def read_file(self, path: str) -> str:
        await self.start()
        real = resolve_in_workspace(self.workspace, path)
        bits, _ = await asyncio.to_thread(self._container.get_archive, real)
        stream = io.BytesIO(b"".join(bits))
        with tarfile.open(fileobj=stream) as tar:
            member = tar.next()
            return tar.extractfile(member).read().decode(errors="replace")

    async def list_files(self, path: str = ".") -> list[str]:
        await self.start()
        real = resolve_in_workspace(self.workspace, path)  # #2：先约束路径再执行
        res = await self.exec(["ls", "-1", real], timeout=10)
        return [ln for ln in res.stdout.splitlines() if ln]
