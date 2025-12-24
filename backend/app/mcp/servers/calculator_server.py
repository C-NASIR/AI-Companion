"""Local MCP server that wraps the calculator tool."""

from __future__ import annotations

from typing import Mapping

from pydantic import ValidationError

from ...tools import CalculatorInput, CalculatorOutput, ToolExecutionError, execute_calculator
from ..schema import ToolCallResult, ToolDescriptor
from ..server import MCPServer


class CalculatorMCPServer(MCPServer):
    """Exposes the calculator through the MCP abstraction."""

    TOOL_NAME = "calculator"

    def __init__(self):
        super().__init__("calculator_server", source="local")
        self._descriptor = ToolDescriptor(
            name=self.TOOL_NAME,
            description="Performs deterministic arithmetic operations.",
            input_schema=CalculatorInput.model_json_schema(),
            output_schema=CalculatorOutput.model_json_schema(),
            permission_scope="calculator.basic",
            source="local",
            server_id=self.server_id,
        )

    async def list_tools(self):
        return [self._descriptor]

    async def call_tool(
        self, *, tool_name: str, arguments: Mapping[str, object]
    ) -> ToolCallResult:
        if tool_name != self.TOOL_NAME:
            raise ValueError(f"calculator server does not handle tool {tool_name}")
        try:
            payload = CalculatorInput.model_validate(arguments)
        except ValidationError as exc:
            return ToolCallResult(
                tool_name=tool_name,
                error={
                    "error": "invalid_arguments",
                    "details": exc.errors(),
                },
            )
        try:
            result = execute_calculator(payload)
        except ToolExecutionError as exc:
            return ToolCallResult(tool_name=tool_name, error=exc.error_payload.model_dump())
        if not isinstance(result, CalculatorOutput):
            result = CalculatorOutput.model_validate(result)
        return ToolCallResult(tool_name=tool_name, output=result.model_dump())
