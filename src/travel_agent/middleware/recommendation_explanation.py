"""Deterministic follow-up explanations for the latest travel recommendation."""

import json
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from travel_agent.domain.multi_stop import MultiStopDraft
from travel_agent.middleware.multi_stop_intent import has_current_multi_stop

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
    "subway_first": "地铁优先",
}


def explain_latest_multi_stop(messages: list[object]) -> str:
    """Use the latest tool snapshot only; never resurrect an older successful draft."""
    fallback = "最新多站点结果不足以解释推荐，请先重新规划完整行程。"
    message = next((item for item in reversed(messages) if isinstance(item, ToolMessage)
                    and item.name == "plan_multi_stop_trip"), None)
    if message is None:
        return fallback
    for payload in reversed(_tool_payloads(message)):
        try:
            draft = MultiStopDraft.model_validate(payload)
        except ValidationError:
            continue
        selection = draft.result.selection
        winner = next((plan for plan in selection.plans
                       if plan.plan_id == selection.recommended_plan_id), None)
        if (selection.status != "feasible" or winner is None
                or winner.evaluation.status != "feasible" or winner.evaluation.totals is None):
            return ("本次没有可确认可行的完整推荐，不能解释为已推荐某种交通方式，"
                    "请调整限制或补齐路线数据。")
        totals = winner.evaluation.totals
        lines = ["多站点系统推荐依据本次完整候选的确定性筛选和排序，不是模型主观选择。",
                 "先检查整段时间线、驾车条件及累计限制，再按"
                 + _PRIORITY_NAMES.get(draft.result.request.preferences.priority, "均衡偏好")
                 + "排序；综合分不是耗时或票价本身，不保证同时最快、最便宜。",
                 "推荐各段：" + " → ".join(_MODE_NAMES[item.leg.option.mode]
                                          for item in winner.evaluation.legs),
                 f"预计总耗时 {totals.elapsed_s / 60:g} 分钟，"
                 f"含等待 {totals.waiting_s / 60:g} 分钟、停留 {totals.stay_s / 60:g} 分钟。"]
        if draft.result.request.preferences.can_drive is False:
            lines.append("不能驾车只排除驾车，不代表只能步行；公共交通仍可参与比较。")
        lines.append("总费用未知。" if totals.total_cost_yuan is None
                     else f"本次路线总费用 {totals.total_cost_yuan:g} 元。")
        if selection.selected_plan_id and selection.selected_plan_id != winner.plan_id:
            lines.append("用户当前选择与系统推荐不同；以上解释针对系统推荐。页面上的手选方案不会被此解释更改。")
        lines.extend(winner.reasons)
        lines.extend(dict.fromkeys([*selection.limitations, *winner.evaluation.limitations]))
        lines.append("其他候选查询失败仅表示本次数据未取得，不代表现实中不存在该交通方式。未计算天气，不保证未来路况或班次。")
        return "\n".join(lines)
    return fallback


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
        if isinstance(message, ToolMessage) and message.name == "plan_multi_stop_trip":
            return None
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

        if has_current_multi_stop(list(messages[:-1])):
            if re.search(r"改|调整|重新|重算|保存|换|增加|减少|删除|添加", content):
                return None
            return {"jump_to": "end", "messages": [AIMessage(
                content=explain_latest_multi_stop(list(messages[:-1])))]}
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
