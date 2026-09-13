"""Load and validate versioned Agent behavior evaluation cases."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DEFAULT_DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "evals"
    / "agent_behavior_cases.json"
)


class EvalCase(BaseModel):
    """One stable user task and its deterministic behavior expectations."""

    name: str = Field(pattern=r"^[a-z0-9_]+$")
    category: Literal["weather", "place", "route"]
    message: str = Field(min_length=1)
    follow_up_messages: tuple[str, ...] = Field(default=(), max_length=3)
    expected_tool: str | None
    expected_args: dict[str, object] = Field(default_factory=dict)
    expected_tool_sequence: tuple[str, ...] = ()
    route_tool_from_endpoints: str | None = None
    planning_tool_from_endpoints: str | None = None
    expected_planning_args: dict[str, object] = Field(default_factory=dict)
    expect_tool_error: bool = False
    expected_tool_error_substrings: tuple[str, ...] = ()
    required_final_substrings: tuple[str, ...] = ()
    required_final_any_of: tuple[str, ...] = ()
    forbidden_final_substrings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_expectations(self) -> "EvalCase":
        """Reject contradictory expectations before an expensive model run."""
        if any(not message.strip() for message in self.follow_up_messages):
            raise ValueError("多轮评测的后续消息不能为空")
        if self.expected_tool is None and self.expected_args:
            raise ValueError("无工具案例不能声明 expected_args")
        if (
            self.expected_tool_sequence
            and self.expected_tool != self.expected_tool_sequence[0]
        ):
            raise ValueError("expected_tool 必须是工具序列的第一项")
        if (
            self.route_tool_from_endpoints is not None
            and self.route_tool_from_endpoints
            not in self.expected_tool_sequence
        ):
            raise ValueError("路线工具必须出现在 expected_tool_sequence 中")
        if (
            self.planning_tool_from_endpoints is not None
            and self.planning_tool_from_endpoints
            not in self.expected_tool_sequence
        ):
            raise ValueError("综合规划工具必须出现在 expected_tool_sequence 中")
        if (
            self.route_tool_from_endpoints is not None
            and self.planning_tool_from_endpoints is not None
        ):
            raise ValueError("单一路线工具与综合规划工具不能同时声明")
        if self.expected_planning_args and self.planning_tool_from_endpoints is None:
            raise ValueError("综合规划参数期望必须同时声明 planning_tool_from_endpoints")
        if self.expected_tool_error_substrings and not self.expect_tool_error:
            raise ValueError("工具错误文本期望必须同时启用 expect_tool_error")
        return self


class EvalDataset(BaseModel):
    """One versioned collection of deterministic evaluation cases."""

    schema_version: Literal[1]
    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_names(self) -> "EvalDataset":
        """Require stable unique identifiers for metric comparisons."""
        names = [case.name for case in self.cases]
        if len(names) != len(set(names)):
            raise ValueError("评测案例 name 不能重复")
        return self


def load_eval_dataset(path: Path = DEFAULT_DATASET_PATH) -> EvalDataset:
    """Read UTF-8 JSON and validate the complete evaluation dataset."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return EvalDataset.model_validate(payload)
