from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ValidationError

from ..types import ToolCall, ToolResult


class Tool(ABC):
    name: str
    description: str
    Params: type[BaseModel]

    @abstractmethod
    async def run(self, params: BaseModel) -> str:
        ...

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.Params.model_json_schema(),
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, max_chars: int | None = None) -> None:
        self._registry = registry
        self._max_chars = max_chars

    def _truncate(self, text: str) -> str:
        if self._max_chars is not None and len(text) > self._max_chars:
            return text[: self._max_chars] + "…(已截断)"
        return text

    async def execute(self, call: ToolCall) -> ToolResult:
        tool = self._registry.get(call.name)
        if tool is None:
            return ToolResult(call.id, f"未知工具: {call.name}", is_error=True)
        try:
            params = tool.Params.model_validate(call.arguments)
        except ValidationError as e:
            return ToolResult(call.id, f"参数校验失败: {e}", is_error=True)
        try:
            content = await tool.run(params)
            return ToolResult(call.id, self._truncate(content), is_error=False)
        except Exception as e:  # 工具内部异常兜成 is_error，喂回模型自纠正
            return ToolResult(call.id, f"工具执行出错: {e}", is_error=True)
