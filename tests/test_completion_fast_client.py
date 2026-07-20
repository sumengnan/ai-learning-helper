from app.completion import build_fast_client
from app.config import AppConfig


class _Dummy:
    """占位主 client。"""


def test_fast_client_falls_back_to_main_when_unset():
    cfg = AppConfig(api_key="k", fast_model="", _env_file=None)
    c = _Dummy()
    client, model = build_fast_client(c, cfg)
    assert client is c and model == cfg.model   # 未配 fast_model → 回退主 client/主模型


def test_fast_client_uses_fast_model_when_set():
    cfg = AppConfig(api_key="k", fast_model="fast-m", _env_file=None)
    c = _Dummy()
    client, model = build_fast_client(c, cfg)
    assert model == "fast-m"      # 用 fast 模型
    assert client is not c        # 起了指向 fast 模型的独立 client
