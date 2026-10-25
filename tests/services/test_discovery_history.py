import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import aiosqlite
import pytest

from tests.services.test_discovery import discovery_request, planned
from travel_agent.domain.discovery import (
    DiscoveryChooseInput,
    DiscoveryRecommendInput,
    DiscoveryRefineRequest,
    DiscoverySelectionInput,
)
from travel_agent.services.conversation import (
    ConversationRepository,
    ConversationRepositoryError,
    ConversationRevisionConflict,
)
from travel_agent.services.discovery import DiscoveryService
from travel_agent.services.discovery_history import DiscoveryHistoryService
from travel_agent.services.multi_stop_workspace import MultiStopWorkspace


def build(tmp_path):
    repository = ConversationRepository(tmp_path / "history.sqlite")
    planner = AsyncMock()
    planner.plan.side_effect = planned
    workspace = MultiStopWorkspace(tmp_path / "drafts.sqlite")
    service = DiscoveryHistoryService(repository, DiscoveryService(planner), workspace, planner)
    return repository, planner, workspace, service


async def start(service, thread=None, **changes):
    value = DiscoveryRecommendInput(**discovery_request(
        departure_at=None, **changes).model_dump(), thread_id=thread or uuid4())
    return await service.start(value)


async def test_restart_preserves_recommendation_pending_edits_and_visible_turns(tmp_path):
    repository, planner, _, handler = build(tmp_path)
    first = await start(handler)
    thread = first.history.thread_id
    before = await repository.get_conversation(thread)
    assert before.discovery.revision == 1
    assert len(before.messages) == 2
    reply = await handler.refine(DiscoveryRefineRequest(
        thread_id=thread, expected_revision=1, request=before.discovery.request,
        message="只有两小时"))
    assert reply.status == "clarification"
    assert reply.history.revision == 2
    fresh_repository = ConversationRepository(tmp_path / "history.sqlite")
    detail = await fresh_repository.get_conversation(thread)
    assert detail.discovery.pending_changes.model_dump(exclude_unset=True) == {
        "available_minutes": 120,
    }
    assert detail.discovery.result == before.discovery.result
    assert len(detail.messages) == 4
    assert detail.messages[:2] == before.messages
    planner.reset_mock()
    fresh_handler = DiscoveryHistoryService(
        fresh_repository, handler.discovery, handler.workspace, planner)
    # Browser-submitted baseline is deliberately different; only the saved state is used.
    reply = await fresh_handler.refine(DiscoveryRefineRequest(
        thread_id=thread, expected_revision=2,
        request=discovery_request(departure_at=None, available_minutes=700, stay_minutes=15),
        message="停留改成60分钟"))
    assert reply.request.available_minutes == 120
    assert reply.request.stay_minutes == 60
    assert reply.history.revision == 3
    assert len((await fresh_repository.get_conversation(thread)).messages) == 6
    assert planner.plan.await_count == 3


async def test_threads_keep_separate_constraints_and_stale_updates_do_not_append_messages(tmp_path):
    repository, planner, _, handler = build(tmp_path)
    first = await start(handler, available_minutes=240)
    second = await start(handler, available_minutes=180, stay_minutes=60)
    first_detail = await repository.get_conversation(first.history.thread_id)
    second_detail = await repository.get_conversation(second.history.thread_id)
    await handler.refine(DiscoveryRefineRequest(
        thread_id=second.history.thread_id, expected_revision=1,
        request=first_detail.discovery.request, message="预算改成50元"))
    assert (await repository.get_conversation(first.history.thread_id)) == first_detail
    updated = await repository.get_conversation(second.history.thread_id)
    assert updated.discovery.request.available_minutes == 180
    assert updated.discovery.request.stay_minutes == 60
    assert updated.discovery.request.budget_yuan == 50
    planner.reset_mock()
    with pytest.raises(ConversationRevisionConflict):
        await handler.refine(DiscoveryRefineRequest(
            thread_id=second.history.thread_id, expected_revision=1,
            request=second_detail.discovery.request, message="预算改成10元"))
    assert await repository.get_conversation(second.history.thread_id) == updated
    planner.plan.assert_not_awaited()


async def test_choice_and_manual_selection_restore_complete_draft_without_saving(tmp_path):
    repository, _, workspace, handler = build(tmp_path)
    first = await start(handler)
    thread = first.history.thread_id
    chosen = await handler.choose(thread, DiscoveryChooseInput(
        expected_revision=1, attraction_id=first.candidates[0].card.attraction_id))
    detail = await repository.get_conversation(thread)
    assert detail.discovery.stage == "journey"
    assert detail.discovery.draft_id == chosen.draft.draft_id
    assert detail.messages[-1].cards[0]["draft_id"] == str(chosen.draft.draft_id)
    different = chosen.draft.result.selection.plans[-1].plan_id
    changed = await handler.selection(thread, DiscoverySelectionInput(
        expected_revision=2, draft_id=chosen.draft.draft_id, plan_id=different))
    assert changed.revision >= 2
    current = await repository.get_conversation(thread)
    assert current.discovery.plan_id == different
    assert len(current.messages) == 4
    assert current.messages == detail.messages
    same = await handler.selection(thread, DiscoverySelectionInput(
        expected_revision=changed.revision, draft_id=chosen.draft.draft_id, plan_id=different))
    assert same.revision == changed.revision
    assert await workspace.list_saved() == []


async def test_history_deletion_cascades_state_and_old_plain_history_stays_readable(tmp_path):
    repository, _, _, handler = build(tmp_path)
    first = await start(handler)
    old_thread = uuid4()
    await repository.upsert_conversation(old_thread, "旧普通会话")
    await repository.append_message(old_thread, "user", "普通消息")
    assert (await repository.get_conversation(old_thread)).discovery is None
    assert await repository.delete_conversation(first.history.thread_id)
    async with aiosqlite.connect(tmp_path / "history.sqlite") as database:
        cursor = await database.execute("SELECT count(*) FROM conversation_discovery")
        assert (await cursor.fetchone())[0] == 0
    assert (await repository.get_conversation(old_thread)).messages[0].content == "普通消息"


async def test_concurrent_writes_commit_exactly_one_turn_and_reject_the_stale_one(tmp_path):
    repository, _, _, handler = build(tmp_path)
    first = await start(handler)
    thread = first.history.thread_id
    detail = await repository.get_conversation(thread)
    replies = await asyncio.gather(*[
        repository.store_discovery(thread, detail.discovery, 1,
                                   user_content=f"预算修改{index}", assistant_content="测试回复")
        for index in range(2)
    ], return_exceptions=True)
    assert sum(isinstance(reply, ConversationRevisionConflict) for reply in replies) == 1
    restored = await repository.get_conversation(thread)
    assert restored.discovery.revision == 2
    assert len(restored.messages) == 4


async def test_state_write_failure_rolls_back_messages_and_does_not_replace_prior_state(tmp_path):
    repository, _, _, handler = build(tmp_path)
    first = await start(handler)
    thread = first.history.thread_id
    before = await repository.get_conversation(thread)
    async with aiosqlite.connect(tmp_path / "history.sqlite") as database:
        await database.execute("CREATE TRIGGER reject_state "
                               "BEFORE UPDATE ON conversation_discovery "
                               "BEGIN SELECT RAISE(ABORT, 'test'); END")
        await database.commit()
    with pytest.raises(ConversationRepositoryError):
        await repository.store_discovery(thread, before.discovery, 1,
                                         user_content="测试修改", assistant_content="测试回复")
    assert await repository.get_conversation(thread) == before


async def test_pending_approval_cannot_be_overwritten_by_recommendation(tmp_path):
    repository, planner, _, handler = build(tmp_path)
    first = await start(handler)
    thread = first.history.thread_id
    await repository.set_status(thread, "approval_required")
    planner.reset_mock()
    with pytest.raises(ConversationRevisionConflict):
        await handler.start(DiscoveryRecommendInput(
            **discovery_request(departure_at=None).model_dump(),
            thread_id=thread, expected_revision=1))
    planner.plan.assert_not_awaited()
