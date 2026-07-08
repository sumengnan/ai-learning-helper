from __future__ import annotations

import ast
import operator

from pydantic import BaseModel

from ..base import Tool

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_MAX_POW_EXPONENT = 1000  # 防 9**99999999 类 DoS


def _eval(node: ast.AST):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left = _eval(node.left)
        right = _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POW_EXPONENT:
            raise ValueError(f"幂运算指数过大（|{right}| > {_MAX_POW_EXPONENT}）")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("不支持的表达式（仅允许数字与 + - * / ** % 和括号）")


def safe_eval(expression: str):
    """受限 AST 求值，绝不使用 eval，杜绝任意代码执行。"""
    return _eval(ast.parse(expression, mode="eval").body)


class CalculatorTool(Tool):
    name = "calculator"
    description = "计算一个算术表达式，支持 + - * / ** % 和括号。"

    class Params(BaseModel):
        expression: str

    async def run(self, params: "CalculatorTool.Params") -> str:
        return str(safe_eval(params.expression))
