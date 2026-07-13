import logging

from app.logging_setup import (
    _CorrelationFilter,
    configure_logging,
    set_log_context,
)


def _rec():
    return logging.LogRecord("n", logging.INFO, "p", 1, "msg", (), None)


def test_correlation_filter_injects_fields():
    f = _CorrelationFilter()
    rec = _rec()
    with set_log_context(run_id="r1", conv_id="c1"):
        f.filter(rec)
    assert "run_id=r1" in rec.corr and "conv_id=c1" in rec.corr


def test_correlation_empty_outside_context():
    rec = _rec()
    _CorrelationFilter().filter(rec)
    assert rec.corr == "-"


def test_correlation_skips_empty_fields():
    rec = _rec()
    with set_log_context(run_id="r1", conv_id=""):   # 空值忽略
        _CorrelationFilter().filter(rec)
    assert rec.corr == "run_id=r1"


def test_context_restored_after_with():
    rec = _rec()
    with set_log_context(run_id="r1"):
        pass
    _CorrelationFilter().filter(rec)
    assert rec.corr == "-"                            # 退出后还原


def test_configure_default_is_info(monkeypatch):
    monkeypatch.delenv("HARNESS_LOG_LEVEL", raising=False)
    configure_logging()
    assert logging.getLogger().level == logging.INFO


def test_configure_env_override(monkeypatch):
    monkeypatch.setenv("HARNESS_LOG_LEVEL", "WARNING")
    configure_logging()
    assert logging.getLogger().level == logging.WARNING
    configure_logging("INFO")   # 复位，避免污染其它测试


def test_configure_idempotent_single_handler():
    configure_logging()
    configure_logging()
    n = sum(getattr(h, "_harness_configured", False)
            for h in logging.getLogger().handlers)
    assert n == 1


def test_format_has_full_timestamp():
    configure_logging()
    handler = next(h for h in logging.getLogger().handlers
                   if getattr(h, "_harness_configured", False))
    rec = _rec()
    _CorrelationFilter().filter(rec)
    out = handler.formatter.format(rec)
    # 形如 2026-07-13 16:00:00.123 INFO [-] n: msg —— 含日期+时分秒+毫秒
    import re
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} ", out)
