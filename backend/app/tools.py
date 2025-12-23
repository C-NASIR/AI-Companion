"""Tool registry, schemas, and calculator implementation for Session 4."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field


class ToolExecutionError(Exception):
    """Raised when a tool fails with a structured error payload."""

    def __init__(self, error_payload: BaseModel):
        super().__init__(error_payload.model_dump_json())
        self.error_payload = error_payload


class ToolInputModel(BaseModel):
    """Base class with common config for tool schemas."""

    model_config = ConfigDict(extra="forbid")


class ToolOutputModel(BaseModel):
    """Base class for tool outputs."""

    model_config = ConfigDict(extra="forbid")


class ToolErrorModel(BaseModel):
    """Base class for tool errors."""

    model_config = ConfigDict(extra="forbid")

    error: str


ToolExecuteFn = Callable[[ToolInputModel], ToolOutputModel]


@dataclass(frozen=True)
class ToolSpec:
    """Declarative definition of a tool."""

    name: str
    description: str
    input_model: type[ToolInputModel]
    output_model: type[ToolOutputModel]
    error_model: type[ToolErrorModel]
    execute: ToolExecuteFn


class ToolRegistry:
    """In-memory registry of available tools."""

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool name {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list(self) -> Iterable[ToolSpec]:
        return self._tools.values()


# ----- Calculator tool -----------------------------------------------------


class CalculatorOperation(str, Enum):
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"


class CalculatorInput(ToolInputModel):
    operation: CalculatorOperation
    a: float = Field(..., description="First operand")
    b: float = Field(..., description="Second operand")


class CalculatorOutput(ToolOutputModel):
    result: float


class CalculatorError(ToolErrorModel):
    """Calculator error payload."""


def execute_calculator(payload: CalculatorInput) -> CalculatorOutput:
    if payload.operation == CalculatorOperation.ADD:
        result = payload.a + payload.b
    elif payload.operation == CalculatorOperation.SUBTRACT:
        result = payload.a - payload.b
    elif payload.operation == CalculatorOperation.MULTIPLY:
        result = payload.a * payload.b
    elif payload.operation == CalculatorOperation.DIVIDE:
        if payload.b == 0:
            raise ToolExecutionError(CalculatorError(error="division_by_zero"))
        result = payload.a / payload.b
    else:  # pragma: no cover - enum gate should prevent this
        raise ToolExecutionError(
            CalculatorError(error=f"unsupported operation {payload.operation}")
        )
    return CalculatorOutput(result=result)


REGISTRY = ToolRegistry()
REGISTRY.register(
    ToolSpec(
        name="calculator",
        description="Performs simple arithmetic operations safely.",
        input_model=CalculatorInput,
        output_model=CalculatorOutput,
        error_model=CalculatorError,
        execute=execute_calculator,
    )
)


def get_tool_registry() -> ToolRegistry:
    return REGISTRY


def validate_tool_arguments(spec: ToolSpec, arguments: Mapping[str, object]) -> ToolInputModel:
    """Validate arguments against the tool's declared input schema."""
    return spec.input_model.model_validate(arguments)
