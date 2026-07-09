# src/harness/sandbox/factory.py
from __future__ import annotations

from .local import LocalSandbox


def build_sandbox(config):
    """按 config.sandbox_backend 造 Sandbox 实例。"""
    if config.sandbox_backend == "docker":
        from .docker import DockerSandbox
        return DockerSandbox(
            docker_host=config.sandbox_docker_host, image=config.sandbox_image,
            workspace=config.sandbox_workspace, user=config.sandbox_user,
            network=config.sandbox_network, mem_limit=config.sandbox_mem_limit,
            cpus=config.sandbox_cpus, pids_limit=config.sandbox_pids_limit,
            read_only=config.sandbox_read_only,
            tls_ca_cert=config.sandbox_docker_tls_ca_cert,
            tls_client_cert=config.sandbox_docker_tls_client_cert,
            tls_client_key=config.sandbox_docker_tls_client_key,
            tls_verify=config.sandbox_docker_tls_verify)
    return LocalSandbox()
