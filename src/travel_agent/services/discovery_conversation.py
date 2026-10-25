"""Conservative edits of confirmed discovery conditions; uncertain text never relaxes them."""

import re

from pydantic import ValidationError

from travel_agent.domain.decision import update_preferences
from travel_agent.domain.discovery import (
    DiscoveryChanges,
    DiscoveryRefineRequest,
    DiscoveryRefineResponse,
    DiscoveryRequest,
)
from travel_agent.services.attraction import load_profiles
from travel_agent.services.discovery import DiscoveryService

_NUMBER = r"[零〇一二两三四五六七八九十百千万\d.]+"
_ACTION = r"(?:改成|改为|调整为|调整到|控制在|不超过|上限为|设为|改到|变成|改|是|为)?"
_DURATION = rf"(?P<n>{_NUMBER}|半)(?:个)?(?P<prehalf>半)?(?P<unit>小时|分钟|分)(?P<half>半)?"
_INTENT = re.compile(r"预算|(?:可用|往返|总共|总时|只有|仅有|我有|只剩|停留|游览|参观)"
                     r".*?(?:小时|分钟)|"
                     r"换(?:一)?个|换掉|排除|不去|不要推荐|别推荐|少步行|少换乘|地铁优先|"
                     r"省钱优先|时间优先|综合推荐|不能开车|不能驾车|可以驾车|可以开车|"
                     r"最多步行|最多换乘|取消步行|取消换乘|取消修改|放弃修改|重新推荐|再推荐|"
                     r"恢复(?:全部|所有)候选")
_PREFS = {"综合推荐": "balanced", "时间优先": "fastest", "省钱优先": "cheapest",
          "少步行": "least_walking", "少换乘": "fewest_transfers", "地铁优先": "subway_first"}


def _number(text: str) -> float:
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    if text == "半":
        return 0.5
    if (len(text) > 1 and not re.search(r"十|百|千|万", text)
            or re.search(r"[百千万][一二两三四五六七八九]$", text)):
        raise ValueError("数值表达可能有歧义，请使用阿拉伯数字")
    digits = {character: index for index, character in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2})
    number = section = total = 0
    for character in text:
        if character in digits:
            number = digits[character]
        elif character in {"十", "百", "千"}:
            section += (number or 1) * {"十": 10, "百": 100, "千": 1000}[character]
            number = 0
        elif character == "万":
            total += (section + number) * 10000
            number = section = 0
        else:
            raise ValueError("数值表达不明确")
    return float(total + section + number)


def _minutes(match: re.Match) -> int:
    if match["half"] and match["prehalf"]:
        raise ValueError("时间表达不明确")
    value = _number(match["n"]) + (0.5 if match["half"] or match["prehalf"] else 0)
    value *= 60 if match["unit"] == "小时" else 1
    if not value.is_integer():
        raise ValueError("请用整分钟说明时间")
    return int(value)


def parse_changes(message: str, current_ids: list[str], names: dict[str, str],
                  excluded: list[str]) -> tuple[dict, bool]:
    text = re.sub(r"\s+", "", message).strip("。！!？?")
    changes = {}
    retry = False

    def record(field: str, value):
        if field in changes and changes[field] != value:
            raise ValueError("同一条消息对同一条件给出了不同值，请确认采用哪一个")
        changes[field] = value

    def consume(pattern, apply):
        nonlocal text

        def replace(match):
            apply(match)
            return ""
        text = re.sub(pattern, replace, text)

    consume(r"取消(?:交通与普通门票)?预算(?:限制|上限)?|预算不限|不限预算",
            lambda _: record("budget_yuan", None))
    consume(rf"(?:交通与普通门票)?预算(?:上限)?{_ACTION}(?P<n>{_NUMBER})(?:元|块钱|块)?(?:以内|以下)?",
            lambda match: record("budget_yuan", _number(match["n"])))
    consume(rf"(?:停留|游览|参观)(?:时间|时长)?{_ACTION}{_DURATION}",
            lambda match: record("stay_minutes", _minutes(match)))
    consume(rf"(?:往返(?:总共|共)|往返(?:总)?(?:可用)?(?:时间|时长)?|总可用(?:时间|时长)?|"
            rf"可用(?:时间|时长)|总共|总时长|只有|仅有|我只有|我有|只剩){_ACTION}{_DURATION}",
            lambda match: record("available_minutes", _minutes(match)))
    consume(rf"最多步行(?P<n>{_NUMBER})(?P<unit>公里|千米|米)",
            lambda match: record("max_walking_distance_m", _number(match["n"]) * (
                1000 if match["unit"] in {"公里", "千米"} else 1)))
    consume(rf"最多换乘(?P<n>{_NUMBER})次",
            lambda match: record("max_transfer_count", _number(match["n"])))
    consume(r"取消步行(?:限制|上限)", lambda _: record("max_walking_distance_m", None))
    consume(r"取消换乘(?:限制|上限)", lambda _: record("max_transfer_count", None))
    consume(r"不能(?:开车|驾车)", lambda _: record("can_drive", False))
    consume(r"可以(?:开车|驾车)", lambda _: record("can_drive", True))
    for label, value in _PREFS.items():
        consume(rf"{_ACTION}(?:优先)?{label}", lambda _, value=value: record("priority", value))
    if re.search(r"(?:换(?:一)?个|换掉|排除|不去|不要推荐|别推荐|恢复(?:全部|所有)候选)", text):
        if re.search(r"取消排除|恢复(?:全部|所有)候选", text):
            record("excluded_attraction_ids", [])
            text = re.sub(r"取消排除|恢复(?:全部|所有)候选", "", text)
        else:
            mentioned = [identity for identity, name in names.items() if name in text]
            if len(mentioned) > 1:
                raise ValueError("请一次明确要排除的馆区，避免混用不同地点")
            if not mentioned and not re.search(r"换(?:一)?个", text):
                raise ValueError("请说明要排除的具体馆区名称")
            target = mentioned[0] if mentioned else (current_ids[0] if current_ids else None)
            if target is None:
                raise ValueError("当前没有可替换的候选，请说明具体馆区或先调整条件重新推荐")
            for name in names.values():
                text = text.replace(name, "")
            text = re.sub(r"换(?:一)?个(?:目的地|景点|馆区)?|换掉|排除|不去|不要推荐|别推荐",
                          "", text)
            record("excluded_attraction_ids", list(dict.fromkeys([*excluded, target])))
    text = re.sub(r"重新推荐|再推荐|重新计算|重新算一下", "", text, count=1)
    if not changes and re.search(r"重新推荐|再推荐|重新计算|重新算一下", message):
        retry = True
    text = re.sub(r"请|帮我|麻烦|谢谢|一下|看看|吧|并且|同时|另外|把|我|[，,。；;！!？?]", "", text)
    if text:
        raise ValueError("还有未明确的条件，请分别说明预算、往返总时长、停留或出行限制；"
                         "需要更换出发地点、日期或天气策略时可重新打开“帮我选目的地”")
    return changes, retry


class DiscoveryConversationService:
    def __init__(self, discovery: DiscoveryService | None = None):
        self._discovery = discovery or DiscoveryService()

    async def refine(self, value: DiscoveryRefineRequest) -> DiscoveryRefineResponse:
        baseline = value.request
        pending = value.pending_changes

        def response(status, answer, *, changes=pending, request=baseline, result=None):
            return DiscoveryRefineResponse(status=status, answer=answer, request=request,
                                           pending_changes=changes, result=result)

        text = value.message.strip().rstrip("。！!")
        if text in {"取消修改", "放弃修改"}:
            return response("cancelled", "已取消待确认修改，保留上一次推荐条件和结果。",
                            changes=None)
        if pending and (re.fullmatch(_DURATION, text) or text in {"好", "好的", "可以"}):
            return response("clarification", "请明确是往返总时长还是游览停留，"
                            "例如“停留改成60分钟”。"
                            "待确认修改与上一次结果仍保留，不会凭确认词自动套用。")
        if not _INTENT.search(text):
            return response("not_applicable", "这条消息不是明确的目的地推荐条件修改。")
        profiles = [profile for profile in load_profiles() if profile.enabled]
        names = {profile.card.attraction_id: profile.card.name for profile in profiles}
        referenced = [*baseline.excluded_attraction_ids, *value.candidate_ids]
        if pending:
            referenced.extend(pending.excluded_attraction_ids or [])
        if value.selected_attraction_id:
            referenced.append(value.selected_attraction_id)
        if not set(referenced) <= names.keys():
            raise ValueError("推荐上下文中的目的地不属于现有馆区范围")
        updates = pending.model_dump(exclude_unset=True) if pending else {}
        excluded = updates.get("excluded_attraction_ids", baseline.excluded_attraction_ids)
        targets = ([value.selected_attraction_id] if value.selected_attraction_id
                   else value.candidate_ids)
        try:
            parsed, retry = parse_changes(text, targets, names, excluded)
            if not parsed and not retry:
                return response("clarification", "请说明需要修改的具体条件。")
            updates.update(parsed)
            changes = DiscoveryChanges.model_validate(updates)
        except ValidationError:
            return response("clarification", "这次修改尚未应用。请用明确且不冲突的条件说明，"
                            "例如“预算改成50元”“只有两小时，停留改成60分钟”“换一个目的地”。"
                            "预算范围0–100000元，往返30–720分钟，停留15–480分钟；其他条件保留。")
        except ValueError as exc:
            return response("clarification", f"这次修改尚未应用：{exc}。原条件和结果保留。")
        data = baseline.model_dump()
        preferences = {key: updates[key] for key in {
            "priority", "can_drive", "max_walking_distance_m", "max_transfer_count",
        } if key in updates}
        try:
            data.update({key: item for key, item in updates.items() if key not in preferences})
            data["preferences"] = update_preferences(baseline.preferences, preferences)
            revised = DiscoveryRequest.model_validate(data)
        except ValidationError:
            total = updates.get("available_minutes", baseline.available_minutes)
            stay = updates.get("stay_minutes", baseline.stay_minutes)
            return response("clarification", f"待确认往返共 {total} 分钟、停留 {stay} 分钟；"
                            "总可用时间必须大于停留时间，并预留往返交通。请补充停留时长或调整总时长，"
                            "不会自动缩短停留。原条件和结果仍保留，也可以说“取消修改”。",
                            changes=changes)
        result = await self._discovery.recommend(revised)
        budget = ("不设预算上限" if revised.budget_yuan is None
                  else f"预算 {revised.budget_yuan:g} 元")
        exclusions = "、".join(names[identity] for identity in revised.excluded_attraction_ids)
        return response("updated", f"已按{budget}、往返 {revised.available_minutes} 分钟、"
                        f"停留 {revised.stay_minutes} 分钟重新核算，未修改的条件保留。"
                        + (f"已排除：{exclusions}。" if exclusions else "")
                        + ("当前没有满足条件的候选，不自动放宽限制或扩展景点库。"
                           if not result.candidates else "新的目的地候选已放到右侧。"),
                        changes=None, request=revised, result=result)
