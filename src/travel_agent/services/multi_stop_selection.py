"""Bounded, exhaustive selection of adjacent routes without reordering stops."""

import hashlib
import json
from itertools import product
from math import prod

from travel_agent.domain.multi_stop import (
    LegCandidates,
    MultiStopEvaluation,
    MultiStopRequest,
    MultiStopScores,
    MultiStopSelection,
    RankedMultiStopPlan,
    TimelineIssue,
)
from travel_agent.services.multi_stop_timeline import evaluate_fixed_order
from travel_agent.services.travel_decision import PRIORITY_WEIGHTS


class MultiStopSearchBudgetError(ValueError):
    """Reject oversized searches instead of silently truncating viable alternatives."""


def select_multi_stop_plans(
    request: MultiStopRequest,
    segments: list[LegCandidates],
    *,
    max_candidates_per_leg: int = 8,
    max_combinations: int = 4096,
    limit: int = 3,
    selected_candidate_ids: list[str] | None = None,
) -> MultiStopSelection:
    """Rank only completely feasible combinations; all candidates inside budget are checked."""
    if not 1 <= max_candidates_per_leg <= 16 or not 1 <= max_combinations <= 65536:
        raise ValueError("候选与组合预算超出允许范围")
    if not 1 <= limit <= 10:
        raise ValueError("返回方案数量必须为 1 到 10")
    request = MultiStopRequest.model_validate(request.model_dump())
    segments = [LegCandidates.model_validate(segment.model_dump()) for segment in segments]
    if len(segments) != len(request.stops) - 1:
        raise ValueError("候选分段数必须等于地点数减一")
    for index, segment in enumerate(segments):
        if (
            segment.origin_stop_id != request.stops[index].stop_id
            or segment.destination_stop_id != request.stops[index + 1].stop_id
        ):
            raise ValueError("候选分段必须按相邻地点顺序提供")
        if len(segment.candidates) > max_candidates_per_leg:
            raise MultiStopSearchBudgetError(f"第 {index + 1} 段候选超过预算，未执行搜索")
    count = prod(len(segment.candidates) for segment in segments)
    if selected_candidate_ids is not None:
        if len(selected_candidate_ids) != len(segments) or any(
            not any(leg.candidate_id == identity for leg in segment.candidates)
            for identity, segment in zip(selected_candidate_ids, segments, strict=True)
        ):
            raise ValueError("指定分段候选不存在于当前草案")
    if count > max_combinations:
        raise MultiStopSearchBudgetError(f"组合数 {count} 超过预算 {max_combinations}，未执行搜索")
    empty = [index + 1 for index, segment in enumerate(segments) if not segment.candidates]
    if empty:
        return MultiStopSelection(
            status="insufficient_data",
            evaluated_combinations=0,
            feasible_combinations=0,
            rejected_combinations=0,
            insufficient_combinations=0,
            issue_examples=[
                TimelineIssue(
                    code="route_unavailable",
                    stop_index=index,
                    missing_data=True,
                    message=f"第 {index} 段缺少路线候选。",
                )
                for index in empty
            ],
        )

    feasible = []
    rejected = insufficient = 0
    issue_counts = {}
    examples = {}
    selected_evaluation = None
    ordered = [sorted(segment.candidates, key=lambda leg: leg.candidate_id) for segment in segments]
    for chosen in product(*ordered):
        evaluation = evaluate_fixed_order(request, list(chosen))
        if (
            selected_candidate_ids is not None
            and [leg.candidate_id for leg in chosen] == selected_candidate_ids
        ):
            selected_evaluation = evaluation
        if evaluation.status == "feasible":
            # Share our isolated candidate snapshots while searching, rather than keeping
            # thousands of duplicate geometry arrays. Returned top plans are copied below.
            for timing, candidate in zip(evaluation.legs, chosen, strict=True):
                timing.leg = candidate
            feasible.append(evaluation)
        else:
            rejected += evaluation.status == "infeasible"
            insufficient += evaluation.status == "insufficient_data"
            for issue in evaluation.issues:
                issue_counts[issue.code] = issue_counts.get(issue.code, 0) + 1
                examples.setdefault((issue.code, issue.stop_index), issue)

    plans, limitations = _rank(request, feasible)
    selected = None
    displayed = plans[:limit]
    if selected_candidate_ids is not None:
        selected = next(
            (
                plan
                for plan in plans
                if [item.leg.candidate_id for item in plan.evaluation.legs]
                == selected_candidate_ids
            ),
            None,
        )
        if selected is None:
            evidence = "；".join(issue.message for issue in selected_evaluation.issues)
            raise ValueError(f"所选分段组合无法作为可行行程采用：{evidence}")
        if selected not in displayed:
            displayed = [*displayed, selected]
    if insufficient:
        limitations.append("部分组合因数据不足无法确认可行，推荐仅在已验证可行组合中比较。")
    status = "feasible" if plans else "insufficient_data" if insufficient else "infeasible"
    return MultiStopSelection(
        status=status,
        recommended_plan_id=plans[0].plan_id if plans else None,
        selected_plan_id=selected.plan_id if selected else plans[0].plan_id if plans else None,
        plans=[plan.model_copy(deep=True) for plan in displayed],
        evaluated_combinations=count,
        feasible_combinations=len(feasible),
        rejected_combinations=rejected,
        insufficient_combinations=insufficient,
        issue_counts=issue_counts,
        issue_examples=list(examples.values())[:20],
        limitations=limitations,
    )


def _metric_scores(values: list[float | None], *, missing: float) -> list[float]:
    known = [value for value in values if value is not None]
    if not known:
        return [missing] * len(values)
    low, high = min(known), max(known)
    return [
        missing if value is None else 100 if low == high else 100 * (high - value) / (high - low)
        for value in values
    ]


def _rank(
    request: MultiStopRequest,
    evaluations: list[MultiStopEvaluation],
) -> tuple[list[RankedMultiStopPlan], list[str]]:
    limitations = [
        "只比较本次获得的相邻分段候选，不保证覆盖所有现实路线。",
        "当前推荐比较总耗时、已知费用、步行与换乘，尚未考虑天气。",
    ]
    if not evaluations:
        return [], limitations
    priority = request.preferences.priority
    metric_values = {
        "time": [item.totals.elapsed_s for item in evaluations],
        "cost": [item.totals.total_cost_yuan for item in evaluations],
        "walking": [item.totals.walking_distance_m for item in evaluations],
        "transfers": [item.totals.transfer_count for item in evaluations],
    }
    if priority == "cheapest" and all(value is None for value in metric_values["cost"]):
        limitations.append("全部可行组合的总费用未知，无法按费用比较，已按综合偏好回退。")
        priority = "balanced"
    for preference, metric, label in (
        ("least_walking", "walking", "步行距离"),
        ("fewest_transfers", "transfers", "换乘次数"),
    ):
        if priority == preference and all(value is None for value in metric_values[metric]):
            limitations.append(f"全部可行组合的{label}未知，已按综合偏好回退。")
            priority = "balanced"
    if any(value is None for value in metric_values["cost"]):
        limitations.append("费用未知不按免费处理；省钱优先时完整费用已知的组合优先参与推荐。")
    weights = {
        key: value
        for key, value in PRIORITY_WEIGHTS[
            "balanced" if priority == "subway_first" else priority
        ].items()
        if key != "weather_fit"
    }
    weight_total = sum(weights.values())
    primary_metric = {
        "cheapest": "cost",
        "least_walking": "walking",
        "fewest_transfers": "transfers",
    }.get(priority)
    scores = {
        key: _metric_scores(values, missing=0 if primary_metric == key else 50)
        for key, values in metric_values.items()
    }
    plans = []
    primary_missing = {}
    priority_names = {
        "balanced": "综合推荐",
        "fastest": "时间优先",
        "cheapest": "省钱优先",
        "least_walking": "少步行",
        "fewest_transfers": "少换乘",
        "subway_first": "地铁优先",
    }
    for index, evaluation in enumerate(evaluations):
        values = {key: round(values[index], 6) for key, values in scores.items()}
        total = round(
            sum(values[key] * weight for key, weight in weights.items()) / weight_total,
            6,
        )
        identity_request = request.model_dump(mode="json")
        if identity_request.get("max_elapsed_minutes") is None:
            identity_request.pop("max_elapsed_minutes", None)
        # Preserve identifiers of existing snapshots without a budget.
        if request.max_transport_cost_yuan is None:
            identity_request.pop("max_transport_cost_yuan", None)
        identity = json.dumps(
            {
                "request": identity_request,
                "legs": [item.leg.model_dump(mode="json") for item in evaluation.legs],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        subway_legs = sum(
            item.leg.option.mode == "transit" and item.leg.option.has_subway
            for item in evaluation.legs
        )
        plan = RankedMultiStopPlan(
            plan_id=hashlib.sha256(identity.encode()).hexdigest(),
            evaluation=evaluation,
            scores=MultiStopScores(**values, total=total),
            subway_leg_count=subway_legs,
            reasons=[
                f"已满足出行限制，全程预计 {evaluation.totals.elapsed_s / 60:g} 分钟。",
                f"按{priority_names[priority]}比较本次获得的完整行程。",
                *([f"其中 {subway_legs} 段包含地铁。"] if subway_legs else []),
            ],
        )
        primary_missing[plan.plan_id] = (
            primary_metric is not None and metric_values[primary_metric][index] is None
        )
        plans.append(plan)

    def sort_key(plan: RankedMultiStopPlan):
        return (
            -plan.subway_leg_count if priority == "subway_first" else 0,
            primary_missing[plan.plan_id],
            -plan.scores.total,
            plan.evaluation.totals.elapsed_s,
            tuple(item.leg.candidate_id for item in plan.evaluation.legs),
            plan.plan_id,
        )

    plans.sort(key=sort_key)
    if priority == "subway_first" and plans[0].subway_leg_count == 0:
        limitations.append("没有满足全程硬限制的地铁组合，已按综合偏好回退。")
    return plans, limitations
