"""Persist server-computed recommendation turns and their resumable state."""

from uuid import UUID

from travel_agent.domain.discovery import (
    DiscoveryChooseInput,
    DiscoveryChooseResponse,
    DiscoveryHistoryInfo,
    DiscoveryRecommendInput,
    DiscoveryRecommendResponse,
    DiscoveryRefineRequest,
    DiscoveryRefineResponse,
    DiscoveryRequest,
    DiscoveryResult,
    DiscoverySelectionInput,
    DiscoverySnapshot,
)
from travel_agent.services.conversation import (
    ConversationRepository,
    ConversationRepositoryError,
    ConversationRevisionConflict,
)
from travel_agent.services.discovery import DiscoveryService
from travel_agent.services.discovery_conversation import DiscoveryConversationService
from travel_agent.services.multi_stop_planning import MultiStopPlanningService
from travel_agent.services.multi_stop_workspace import MultiStopWorkspace


class DiscoveryHistoryMissing(ConversationRepositoryError):
    pass


def recommendation_text(result: DiscoveryResult, lead: str = "") -> str:
    if result.candidates:
        content = ("按你的条件，可考虑"
                   + "、".join(item.card.name for item in result.candidates) + "。\n\n")
        content += "\n\n".join(
            f"{index}. **{item.card.name}**：{' '.join(item.reasons)} {' '.join(item.warnings)}"
            for index, item in enumerate(result.candidates, 1)
        )
        content += ("\n\n右侧按预计往返耗时展示候选。可以点击“规划去这里”，"
                    "或告诉我“去某个馆区”。开放和预约安排仍需核实。")
    else:
        content = ("本次没有满足条件的目的地候选。右侧保留了未入选原因；"
                   "可调整条件后继续推荐，不会自动放宽限制。")
    return f"{lead}\n\n{content}" if lead else content


class DiscoveryHistoryService:
    def __init__(self, repository: ConversationRepository, discovery: DiscoveryService,
                 workspace: MultiStopWorkspace, planner: MultiStopPlanningService):
        self.repository = repository
        self.discovery = discovery
        self.workspace = workspace
        self.planner = planner

    async def load(self, thread_id: UUID, revision: int) -> DiscoverySnapshot:
        await self.repository.initialize()
        detail = await self.repository.get_conversation(thread_id)
        if detail is None or detail.discovery is None:
            raise DiscoveryHistoryMissing("推荐会话不存在，请重新确认推荐条件")
        if (detail.discovery.revision != revision
                or detail.conversation.status == "approval_required"):
            raise ConversationRevisionConflict("会话已更新或正在等待审批，请重新打开历史对话后继续")
        return detail.discovery

    @staticmethod
    def info(thread_id, snapshot, content=None):
        return DiscoveryHistoryInfo(thread_id=thread_id, revision=snapshot.revision,
                                    assistant_content=content)

    async def start(self, value: DiscoveryRecommendInput) -> DiscoveryRecommendResponse:
        await self.repository.initialize()
        existing = await self.repository.get_conversation(value.thread_id)
        current_revision = existing.discovery.revision if existing and existing.discovery else 0
        if (current_revision != value.expected_revision
                or existing and existing.conversation.status == "approval_required"):
            raise ConversationRevisionConflict("会话已更新或正在等待审批，请重新打开历史对话后继续")
        criteria = DiscoveryRequest.model_validate(value.model_dump(exclude={
            "thread_id", "expected_revision",
        }))
        result = await self.discovery.recommend(criteria)
        content = recommendation_text(result)
        text = (f"请帮我选目的地：从{criteria.origin.display_name}出发并返回，"
                f"往返共可用 {criteria.available_minutes} 分钟，"
                f"停留 {criteria.stay_minutes} 分钟；"
                + (f"预算 {criteria.budget_yuan:g} 元；"
                   if criteria.budget_yuan is not None else "")
                + (f"出发时间 {criteria.departure_at.isoformat()}。" if criteria.departure_at
                   else "暂未确定出发时间。"))
        snapshot = await self.repository.store_discovery(
            value.thread_id, DiscoverySnapshot(request=criteria, result=result),
            value.expected_revision, user_content=text, assistant_content=content,
        )
        return DiscoveryRecommendResponse(**result.model_dump(),
                                          history=self.info(value.thread_id, snapshot, content))

    async def refine(self, value: DiscoveryRefineRequest) -> DiscoveryRefineResponse:
        previous = await self.load(value.thread_id, value.expected_revision)
        # In history mode the server snapshot, not a browser's cached baseline, is authoritative.
        request = DiscoveryRefineRequest(
            request=previous.request, message=value.message,
            candidate_ids=[item.card.attraction_id for item in previous.result.candidates],
            selected_attraction_id=previous.selected_attraction_id,
            pending_changes=previous.pending_changes,
        )
        reply = await DiscoveryConversationService(self.discovery).refine(request)
        if reply.status == "not_applicable":
            return reply
        snapshot = previous.model_copy(deep=True)
        snapshot.pending_changes = reply.pending_changes
        if reply.status == "updated":
            snapshot.request = reply.request
            snapshot.result = reply.result
            snapshot.stage = "candidates"
            snapshot.selected_attraction_id = snapshot.draft_id = snapshot.plan_id = None
            snapshot.notice = ""
            content = recommendation_text(reply.result, reply.answer)
        else:
            snapshot.notice = ("本次修改尚未生效，仍保留上一次推荐结果；"
                               "请补充确认，或说“取消修改”。"
                               if reply.status == "clarification" else "")
            content = reply.answer
        saved = await self.repository.store_discovery(
            value.thread_id, snapshot, value.expected_revision,
            user_content=value.message, assistant_content=content,
        )
        return reply.model_copy(update={"history": self.info(value.thread_id, saved, content)})

    async def choose(self, thread_id: UUID, value: DiscoveryChooseInput) -> DiscoveryChooseResponse:
        previous = await self.load(thread_id, value.expected_revision)
        if previous.pending_changes or previous.notice:
            raise ConversationRevisionConflict("请先确认或取消待修改条件，再选择目的地")
        candidate = next((item for item in previous.result.candidates
                          if item.card.attraction_id == value.attraction_id), None)
        if candidate is None:
            raise ValueError("所选目的地不属于本次可信候选")
        result = await self.planner.plan(candidate.request)
        draft = await self.workspace.create_draft(result)
        snapshot = previous.model_copy(deep=True)
        snapshot.stage = "journey"
        snapshot.selected_attraction_id = value.attraction_id
        snapshot.draft_id = draft.draft_id
        snapshot.plan_id = result.selection.selected_plan_id or result.selection.recommended_plan_id
        content = ("已生成完整行程。右侧可比较方案、查看所有分段和地图；确认后再保存。"
                   "路线为查询时估计。" if result.selection.status == "feasible"
                   else "本次未得到满足条件的完整方案，右侧列出了原因，可调整条件后重新规划。")
        card = {"type": "multi_stop", "draft_id": str(draft.draft_id),
                "stop_names": [stop.name for stop in result.request.stops],
                "status": result.selection.status}
        saved = await self.repository.store_discovery(
            thread_id, snapshot, value.expected_revision,
            user_content=value.message or f"去{candidate.card.name}，请规划完整往返行程。",
            assistant_content=content, cards=[card],
        )
        return DiscoveryChooseResponse(draft=draft, history=self.info(thread_id, saved, content))

    async def selection(
        self, thread_id: UUID, value: DiscoverySelectionInput,
    ) -> DiscoveryHistoryInfo:
        snapshot = await self.load(thread_id, value.expected_revision)
        if snapshot.stage != "journey":
            raise ConversationRevisionConflict("当前会话尚未采用完整行程")
        draft = await self.workspace.get_plan_draft(value.draft_id, value.plan_id)
        if snapshot.draft_id == draft.draft_id and snapshot.plan_id == value.plan_id:
            return self.info(thread_id, snapshot)
        snapshot = snapshot.model_copy(deep=True)
        snapshot.draft_id = draft.draft_id
        snapshot.plan_id = value.plan_id
        saved = await self.repository.store_discovery(thread_id, snapshot, value.expected_revision)
        return self.info(thread_id, saved)
