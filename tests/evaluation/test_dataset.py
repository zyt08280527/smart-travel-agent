import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from travel_agent.evaluation.dataset import (
    DEFAULT_DATASET_PATH,
    EvalDataset,
    load_eval_dataset,
)


def test_default_behavior_dataset_is_versioned_and_valid() -> None:
    dataset = load_eval_dataset()

    assert dataset.schema_version == 1
    assert len(dataset.cases) == 22
    assert len({case.name for case in dataset.cases}) == 22
    assert {case.category for case in dataset.cases} == {
        "weather",
        "place",
        "route",
    }
    assert DEFAULT_DATASET_PATH.name == "agent_behavior_cases.json"


def test_dataset_loader_rejects_duplicate_case_names(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    payload = {
        "schema_version": 1,
        "cases": [
            {
                "name": "duplicate_case",
                "category": "weather",
                "message": "深圳天气",
                "expected_tool": "query_current_weather",
            },
            {
                "name": "duplicate_case",
                "category": "weather",
                "message": "北京天气",
                "expected_tool": "query_current_weather",
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="name 不能重复"):
        load_eval_dataset(path)


def test_dataset_rejects_route_tool_outside_expected_sequence() -> None:
    with pytest.raises(ValidationError, match="路线工具必须出现"):
        EvalDataset.model_validate(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "name": "invalid_route_case",
                        "category": "route",
                        "message": "规划路线",
                        "expected_tool": "resolve_route_endpoints",
                        "expected_tool_sequence": [
                            "resolve_route_endpoints"
                        ],
                        "route_tool_from_endpoints": "plan_driving_route",
                    }
                ],
            }
        )


def test_dataset_rejects_planning_tool_outside_expected_sequence() -> None:
    with pytest.raises(ValidationError, match="综合规划工具必须出现"):
        EvalDataset.model_validate(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "name": "invalid_planning_case",
                        "category": "route",
                        "message": "比较出行方式",
                        "expected_tool": "resolve_route_endpoints",
                        "expected_tool_sequence": [
                            "resolve_route_endpoints"
                        ],
                        "planning_tool_from_endpoints": (
                            "recommend_travel_plan"
                        ),
                    }
                ],
            }
        )


def test_dataset_rejects_planning_args_without_planning_tool() -> None:
    with pytest.raises(ValidationError, match="综合规划参数期望"):
        EvalDataset.model_validate(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "name": "invalid_planning_args_case",
                        "category": "route",
                        "message": "明天下午出发",
                        "expected_tool": None,
                        "expected_planning_args": {
                            "departure_time_text": "明天下午"
                        },
                    }
                ],
            }
        )


def test_dataset_rejects_empty_follow_up_message() -> None:
    with pytest.raises(ValidationError, match="后续消息不能为空"):
        EvalDataset.model_validate(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "name": "empty_follow_up_case",
                        "category": "route",
                        "message": "明天出发",
                        "follow_up_messages": ["  "],
                        "expected_tool": None,
                    }
                ],
            }
        )
