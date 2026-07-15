"""交付门 OTel 插桩：judge 判定与重答次数须出现在 answer_gate span 上，且 provider 真被装配。"""
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.api.chat import emit_gate_span
from app.assembly import Harness
from app.config import AppConfig
from app.conversations import ConversationStore
from app.documents import DocumentStore
from harness.persistence.checkpoint import CheckpointStore
from harness.persistence.trajectory import TrajectoryStore, TrajectorySink
from harness.telemetry.tracer import get_tracer
from harness.tools.base import ToolRegistry


def _harness(make_mock, turns):
    traj = TrajectoryStore(":memory:")
    return Harness(client=make_mock(turns), registry=ToolRegistry(),
                   checkpoint_store=CheckpointStore(":memory:"),
                   trajectory_store=traj, sink=TrajectorySink(traj), system_prompt="你是助手")


def _tracer_and_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def _trace(**kw):
    base = {"attempts": 2, "retries": 1, "ok": True, "degraded": False,
            "history": [
                {"attempt": 1, "run_id": "r1", "ok": False, "failed": ["judge"],
                 "hard_failed": [], "summary": "judge", "critique": "太笼统"},
                {"attempt": 2, "run_id": "r2", "ok": True, "failed": [],
                 "hard_failed": [], "summary": "", "critique": ""}]}
    return {**base, **kw}


def test_gate_span_carries_attempts_and_verdicts():
    tracer, exporter = _tracer_and_exporter()
    emit_gate_span(tracer, _trace(), t0_ns=1_000_000_000)

    (span,) = exporter.get_finished_spans()
    assert span.name == "answer_gate"
    assert span.attributes["app.gate.attempts"] == 2
    assert span.attributes["app.gate.retries"] == 1      # 重答次数
    assert span.attributes["app.gate.ok"] is True
    assert span.attributes["app.gate.degraded"] is False
    # 每次尝试一个 event，带该次的校验结果
    evs = list(span.events)
    assert [e.name for e in evs] == ["verify.attempt", "verify.attempt"]
    assert evs[0].attributes["attempt"] == 1
    assert evs[0].attributes["ok"] is False
    assert evs[0].attributes["failed"] == ("judge",)     # OTel 把序列存成 tuple
    assert evs[0].attributes["run_id"] == "r1"           # 接缝：据此捞被否草稿
    assert evs[1].attributes["ok"] is True


def test_gate_span_uses_explicit_start_time():
    # 显式 t0 而非补发时刻——span 时长须覆盖整个交付门（含重答），否则近似 0
    tracer, exporter = _tracer_and_exporter()
    emit_gate_span(tracer, _trace(), t0_ns=1_000_000_000)
    (span,) = exporter.get_finished_spans()
    assert span.start_time == 1_000_000_000
    assert span.end_time > span.start_time


def test_gate_span_marks_error_when_degraded():
    tracer, exporter = _tracer_and_exporter()
    vt = _trace(ok=False, degraded=True)
    vt["history"][1] = {"attempt": 2, "run_id": "r2", "ok": False, "failed": ["judge"],
                        "hard_failed": [], "summary": "judge", "critique": "还是差"}
    emit_gate_span(tracer, vt, t0_ns=1_000_000_000)

    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR   # 降级交付须能在后端筛出来
    assert "judge" in span.status.description


def test_gate_span_truncates_long_critique():
    tracer, exporter = _tracer_and_exporter()
    vt = _trace()
    vt["history"][0]["critique"] = "很" * 500
    emit_gate_span(tracer, vt, t0_ns=1_000_000_000)

    (span,) = exporter.get_finished_spans()
    assert len(span.events[0].attributes["critique"]) == 200   # 别把整段反馈灌进 span


def test_emit_gate_span_is_noop_without_provider():
    # 默认没装 provider → no-op tracer，不该抛异常（生产默认路径）
    emit_gate_span(get_tracer("test.noop"), _trace(degraded=True), t0_ns=1_000_000_000)


def test_create_app_installs_telemetry(monkeypatch, make_mock, text_turn):
    # 回归：setup_telemetry 曾全仓无调用点，导致 harness/app 里所有插桩永远是 no-op 死代码
    import app.main as main
    seen = []
    monkeypatch.setattr(main, "setup_telemetry", lambda cfg: seen.append(cfg))

    cfg = AppConfig(api_key="k", app_db_path=":memory:", _env_file=None)
    main.create_app(config=cfg, harness=_harness(make_mock, [text_turn("答案")]),
                    store=ConversationStore(":memory:"), doc_store=DocumentStore(":memory:"))
    assert seen == [cfg]        # 且拿到的是 AppConfig，otel_* 字段继承自 HarnessConfig
    assert hasattr(cfg, "otel_enabled") and cfg.otel_enabled is False   # 默认关
