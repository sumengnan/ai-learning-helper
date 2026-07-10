# src/harness/sandbox/factory.py
from __future__ import annotations

from .local import LocalSandbox


def _docker_for(config, image: str):
    """按 config 造一个指定镜像的 DockerSandbox（除 image 外参数完全一致）。"""
    from .docker import DockerSandbox
    return DockerSandbox(
        docker_host=config.sandbox_docker_host, image=image,
        workspace=config.sandbox_workspace, user=config.sandbox_user,
        network=config.sandbox_network, mem_limit=config.sandbox_mem_limit,
        cpus=config.sandbox_cpus, pids_limit=config.sandbox_pids_limit,
        read_only=config.sandbox_read_only,
        tls_ca_cert=config.sandbox_docker_tls_ca_cert,
        tls_client_cert=config.sandbox_docker_tls_client_cert,
        tls_client_key=config.sandbox_docker_tls_client_key,
        tls_verify=config.sandbox_docker_tls_verify)


def build_sandbox(config):
    """按 config.sandbox_backend 造 Sandbox 实例。

    docker 后端下，若配置了 sandbox_images（语言->镜像）则返回按语言路由的
    RoutingSandbox；否则退回单镜像 DockerSandbox（向后兼容）。
    """
    if config.sandbox_backend == "docker":
        if config.sandbox_images:
            from .routing import RoutingSandbox
            images = config.sandbox_images
            return RoutingSandbox(
                images=images, default_language=config.sandbox_default_language,
                factory=lambda lang: _docker_for(config, images[lang]))
        return _docker_for(config, config.sandbox_image)
    return LocalSandbox()
