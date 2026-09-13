"""Deterministic follow-up explanations for the latest travel recommendation."""

import json
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

_EXPLANATION_PATTERN = re.compile(
    r"(?:为什么|为何).*(?:推荐|选择)|(?:推荐|选择).*(?:原因|理由)|解释.*推荐"
)
_NO_PREVIOUS_PLAN = "我还没有可解释的上一轮出行方案，请先提供起点和终点进行路线比较。"
_MODE_NAMES = {"driving": "驾车", "transit": "公共交通", "walking": "步行"}
_PRIORITY_NAMES = {
    "balanced": "均衡偏好",
    "fastest": "优先速度",
    "cheapest": "优先省钱",
    "least_walking": "优先少步行",
    "fewest_transfers": "优先少换乘",
}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _tool_payloads(message: ToolMessage) -> list[dict[str, object]]:
    blocks = message.content if isinstance(message.content, list) else [message.content]
    payloads: list[dict[str, object]] = []
    for block in blocks:
        text = block.get("text") if isinstance(block, dict) else block
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def latest_planning_payload(messages: list[object]) -> dict[str, object] | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            payload = message.additional_kwargs.get("travel_planning_payload")
            if isinstance(payload, dict):
                return payload
        if not isinstance(message, ToolMessage) or message.name != "recommend_travel_plan":
            continue
        for payload in reversed(_tool_payloads(message)):
            if payload.get("ok") is not False:
                return payload
    return None


def _option_parts(scored: object) -> tuple[dict[str, object], dict[str, object]]:
    if not isinstance(scored, dict):
        return {}, {}
    option = scored.get("option")
    scores = scored.get("scores")
    return (
        option if isinstance(option, dict) else {},
        scores if isinstance(scores, dict) else {},
    )


def render_recommendation_explanation(payload: dict[str, object]) -> str | None:
    """Explain one successful planning payload without inventing new facts."""
    context = payload.get("context")
    recommendation = payload.get("recommendation")
    if not isinstance(context, dict) or not isinstance(recommendation, dict):
        return None

    recommended_mode = recommendation.get("recommended_mode")
    ranked = recommendation.get("ranked_options")
    if recommended_mode not in _MODE_NAMES or not isinstance(ranked, list) or not ranked:
        return None

    winner_option, winner_scores = _option_parts(ranked[0])
    winner_total = _number(winner_scores.get("total"))
    if winner_option.get("mode") != recommended_mode or winner_total is None:
        return None

    preferences = context.get("preferences")
    priority = preferences.get("priority") if isinstance(preferences, dict) else None
    priority_name = _PRIORITY_NAMES.get(priority, "均衡偏好")
    mode_name = _MODE_NAMES[recommended_mode]
    lines = [
        f"上次推荐{mode_name}，依据是可解释的规则评分，不是模型主观判断。",
        "",
        "推荐依据：",
        f"- 在{priority_name}下，{mode_name}综合得分为 {winner_total:.1f}，排名第一。",
    ]

    winner_duration = _number(winner_option.get("duration_s"))
    if len(ranked) > 1:
        runner_option, runner_scores = _option_parts(ranked[1])
        runner_mode = runner_option.get("mode")
        runner_total = _number(runner_scores.get("total"))
        if runner_mode in _MODE_NAMES and runner_total is not None:
            gap = winner_total - runner_total
            lines.append(
                f"- 第二名是{_MODE_NAMES[runner_mode]}，得分 {runner_total:.1f}，"
                f"{mode_name}领先 {gap:.1f} 分。"
            )
            runner_duration = _number(runner_option.get("duration_s"))
            if winner_duration is not None and runner_duration is not None:
                difference = round(abs(runner_duration - winner_duration) / 60)
                if difference:
                    relation = "少" if winner_duration < runner_duration else "多"
                    lines.append(
                        f"- 预计耗时约 {round(winner_duration / 60)} 分钟，"
                        f"比{_MODE_NAMES[runner_mode]}{relation} {difference} 分钟。"
                    )

    if winner_duration is not None and len(ranked) == 1:
        lines.append(f"- 该方案预计耗时约 {round(winner_duration / 60)} 分钟。")

    walking_distance = _number(winner_option.get("walking_distance_m"))
    if walking_distance is not None:
        lines.append(f"- 该方案接驳步行约 {walking_distance:.0f} 米。")
    transfers = winner_option.get("transfer_count")
    if isinstance(transfers, int):
        lines.append(f"- 该方案需要换乘 {transfers} 次。")

    weather = context.get("weather")
    if isinstance(weather, dict):
        condition = weather.get("condition")
        weather_score = _number(winner_scores.get("weather_fit"))
        if isinstance(condition, str) and weather_score is not None:
            weather_kind = "出发时段预报" if weather.get("forecast_at") else "当前天气"
            lines.append(
                f"- {weather_kind}为{condition}，{mode_name}的天气适配得分为 "
                f"{weather_score:.1f}。"
            )

    unavailable = recommendation.get("unavailable_options")
    excluded = []
    if isinstance(unavailable, list):
        for option in unavailable:
            if not isinstance(option, dict):
                continue
            excluded_mode = option.get("mode")
            reason = option.get("failure_reason")
            if excluded_mode in _MODE_NAMES and isinstance(reason, str):
                excluded.append(f"{_MODE_NAMES[excluded_mode]}：{reason}")
    if excluded:
        lines.extend(["", "未参与推荐：", *[f"- {item}" for item in excluded]])

    confidence = _number(recommendation.get("confidence"))
    if confidence is not None and confidence < 0.6:
        lines.extend(
            [
                "",
                "结论提示：前两名差距不大；如果你的优先级或约束发生变化，推荐结果可能改变。",
            ]
        )
    return "\n".join(lines)


class RecommendationExplanationMiddleware(AgentMiddleware):
    """Answer recommendation-reason follow-ups before model and tools run."""

    @hook_config(can_jump_to=["end"])
    def before_model(
        self,
        state: AgentState[Any],
        runtime: object,
    ) -> dict[str, Any] | None:
        del runtime
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], HumanMessage):
            return None
        content = messages[-1].content
        if not isinstance(content, str) or not _EXPLANATION_PATTERN.search(content):
            return None

        payload = latest_planning_payload(list(messages[:-1]))
        answer = (
            render_recommendation_explanation(payload)
            if payload is not None
            else _NO_PREVIOUS_PLAN
        )
        return {
            "jump_to": "end",
            "messages": [AIMessage(content=answer or _NO_PREVIOUS_PLAN)],
        }
