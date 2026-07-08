import pytest

from harness.tools.builtins.calculator import CalculatorTool, safe_eval


def test_safe_eval_basic():
    assert safe_eval("(12+8)*3") == 60
    assert safe_eval("2**3") == 8
    assert safe_eval("-5 + 2") == -3


def test_safe_eval_rejects_code():
    with pytest.raises(ValueError):
        safe_eval("__import__('os').system('ls')")
    with pytest.raises(ValueError):
        safe_eval("open('x')")


async def test_calculator_tool_run():
    tool = CalculatorTool()
    result = await tool.run(tool.Params(expression="(12+8)*3"))
    assert result == "60"
