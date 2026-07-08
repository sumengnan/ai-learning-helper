import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness.loop.agent_loop import AgentLoop
from harness.context.manager import ContextManager
from harness.tools.base import ToolRegistry
from harness.tools.builtins.calculator import CalculatorTool
from harness.llm.base import StreamChunk, ToolCallDelta
from harness.usage import Usage


def _tracer_and_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


async def test_run_produces_span_tree(make_mock):
    tracer, exporter = _tracer_and_exporter()
    tool_turn = [
        StreamChunk(type="tool_call", tool_call_delta=ToolCallDelta(
            index=0, id="c1", name="calculator", arguments='{"expression":"1+1"}')),
        StreamChunk(type="done", usage=Usage(10, 5, 15), attempts=1),
    ]
    text_turn = [StreamChunk(type="text", text="答案 2"), StreamChunk(type="done", usage=Usage(3, 2, 5))]
    reg = ToolRegistry()
    reg.register(CalculatorTool())
    loop = AgentLoop(client=make_mock([tool_turn, text_turn]), registry=reg,
                     context=ContextManager(system_prompt="s"), max_steps=5,
                     run_id_factory=lambda: "r1", tracer=tracer)
    _ = [e async for e in loop.run("算 1+1")]

    names = [s.name for s in exporter.get_finished_spans()]
    assert "run" in names
    assert "step" in names
    assert "model_call" in names
    assert "tool_call:calculator" in names
