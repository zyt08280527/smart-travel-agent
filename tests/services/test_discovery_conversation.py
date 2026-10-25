from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from tests.services.test_discovery import discovery_request
from travel_agent.domain.discovery import DiscoveryRefineRequest, DiscoveryResult
from travel_agent.services.attraction import load_profiles
from travel_agent.services.discovery_conversation import DiscoveryConversationService


def context(message, **updates):
    return DiscoveryRefineRequest(
        request=discovery_request(departure_at=None, budget_yuan=100), message=message,
        candidate_ids=[profile.card.attraction_id for profile in load_profiles()], **updates,
    )


def service():
    discovery = AsyncMock()
    discovery.recommend.return_value = DiscoveryResult(candidates=[], excluded=[], limitations=[])
    return DiscoveryConversationService(discovery), discovery


@pytest.mark.parametrize(("message", "field", "expected"), [
    ("预算改成50元", "budget_yuan", 50),
    ("请把预算调整到五十块钱，谢谢", "budget_yuan", 50),
    ("取消预算限制", "budget_yuan", None),
    ("往返总时长改成三个小时", "available_minutes", 180),
    ("我只有两个半小时", "available_minutes", 150),
    ("往返共四小时", "available_minutes", 240),
    ("停留改成半小时", "stay_minutes", 30),
    ("参观时间改成60分钟", "stay_minutes", 60),
])
async def test_explicit_edits_keep_all_unmentioned_conditions(message, field, expected):
    value = context(message)
    original = deepcopy(value.model_dump())
    handler, discovery = service()
    reply = await handler.refine(value)
    assert reply.status == "updated"
    assert getattr(reply.request, field) == expected
    assert reply.request.origin == value.request.origin
    assert reply.request.departure_at is None
    assert reply.request.preferences == value.request.preferences
    assert value.model_dump() == original
    discovery.recommend.assert_awaited_once_with(reply.request)


async def test_conflicting_duration_keeps_pending_edits_until_next_explicit_reply():
    handler, discovery = service()
    first = await handler.refine(context("只有两小时"))
    assert first.status == "clarification"
    assert first.request.available_minutes == 240
    assert first.pending_changes.available_minutes == 120
    discovery.recommend.assert_not_awaited()
    unclear = await handler.refine(context("60分钟", pending_changes=first.pending_changes))
    assert unclear.status == "clarification"
    assert unclear.pending_changes == first.pending_changes
    discovery.recommend.assert_not_awaited()
    second = await handler.refine(context("停留改成60分钟", pending_changes=first.pending_changes))
    assert second.status == "updated"
    assert second.request.available_minutes == 120
    assert second.request.stay_minutes == 60
    assert second.request.budget_yuan == 100
    assert second.pending_changes is None
    discovery.recommend.assert_awaited_once()


async def test_multi_field_edits_are_atomic_and_preferences_keep_hard_limits():
    handler, _ = service()
    reply = await handler.refine(context(
        "预算改成50元，只有两小时，停留改成60分钟，不能驾车，改成少步行"))
    assert reply.status == "updated"
    assert reply.request.budget_yuan == 50
    assert reply.request.available_minutes == 120
    assert reply.request.stay_minutes == 60
    assert reply.request.preferences.can_drive is False
    assert reply.request.preferences.priority == "least_walking"
    assert reply.request.preferences.transit_strategy == "least_walking"


@pytest.mark.parametrize("message", [
    "预算改成50元，预算改成80元", "预算改成-5元", "只有十分钟", "预算改成一百二元",
    "换一个目的地，不要博物馆", "预算改成50元，并且帮我买门票", "停留改成十分钟",
    "往返改成两小时半分钟", "预算改成一二元",
    "排除", "不去",
])
async def test_unclear_out_of_range_or_unsupported_conditions_never_partially_apply(message):
    handler, discovery = service()
    value = context(message)
    reply = await handler.refine(value)
    assert reply.status == "clarification"
    assert reply.request == value.request
    assert reply.pending_changes is None
    discovery.recommend.assert_not_awaited()


async def test_swap_excludes_only_the_previous_suggestion_and_keeps_existing_exclusions():
    handler, discovery = service()
    value = context("换一个目的地")
    first = await handler.refine(value)
    target = value.candidate_ids[0]
    assert first.request.excluded_attraction_ids == [target]
    second_value = value.model_copy(deep=True)
    second_value.request = first.request
    second_value.candidate_ids = value.candidate_ids[1:]
    second = await handler.refine(second_value)
    assert second.request.excluded_attraction_ids == value.candidate_ids[:2]
    assert second.request.budget_yuan == 100
    assert discovery.recommend.await_count == 2


async def test_swap_after_selection_targets_the_selected_venue_and_can_restore_explicitly():
    value = context("换一个目的地")
    value.selected_attraction_id = value.candidate_ids[-1]
    handler, _ = service()
    reply = await handler.refine(value)
    assert reply.request.excluded_attraction_ids == [value.selected_attraction_id]
    restored = await handler.refine(context("取消排除", pending_changes=reply.pending_changes))
    assert restored.request.excluded_attraction_ids == []


async def test_cancellation_and_unrelated_chat_do_not_call_external_routes():
    handler, discovery = service()
    pending = (await handler.refine(context("只有两小时"))).pending_changes
    cancelled = await handler.refine(context("取消修改", pending_changes=pending))
    assert cancelled.status == "cancelled"
    assert cancelled.pending_changes is None
    unrelated = await handler.refine(context("深圳大学到深圳北站怎么走"))
    assert unrelated.status == "not_applicable"
    discovery.recommend.assert_not_awaited()


async def test_unknown_venue_context_is_rejected_without_querying():
    handler, discovery = service()
    value = context("换一个目的地")
    value.candidate_ids = ["invented-venue"]
    with pytest.raises(ValueError, match="馆区范围"):
        await handler.refine(value)
    discovery.recommend.assert_not_awaited()


async def test_walking_and_transfer_edits_can_be_explicitly_removed():
    handler, _ = service()
    reply = await handler.refine(context("最多步行一公里，最多换乘两次"))
    assert reply.status == "updated"
    assert reply.request.preferences.max_walking_distance_m == 1000
    assert reply.request.preferences.max_transfer_count == 2
    value = context("取消步行限制，取消换乘上限")
    value.request = reply.request
    reply = await handler.refine(value)
    assert reply.request.preferences.max_walking_distance_m is None
    assert reply.request.preferences.max_transfer_count is None
