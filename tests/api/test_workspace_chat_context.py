from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.api.test_app import FakeRuntime
from tests.api.test_multi_stop import fixture_result
from travel_agent.api.app import create_app
from travel_agent.api.runtime import chat_input
from travel_agent.middleware.multi_stop_intent import has_current_multi_stop
from travel_agent.services.multi_stop_workspace import MultiStopWorkspace


class CapturingRuntime(FakeRuntime):
    def __init__(self):
        super().__init__()
        self.drafts = []

    async def chat(self, message, thread_id=None, *, multi_stop_draft=None):
        self.drafts.append(multi_stop_draft)
        return await super().chat(message, thread_id)

    async def stream_chat(self, message, thread_id=None, *, multi_stop_draft=None):
        self.drafts.append(multi_stop_draft)
        async for event in super().stream_chat(message, thread_id):
            yield event


@pytest.mark.parametrize("stream", [False, True])
async def test_chat_reads_complete_server_snapshot_and_manual_selection_without_mutating_it(
    tmp_path, stream,
):
    workspace = MultiStopWorkspace(tmp_path / "context.sqlite")
    draft = await workspace.create_draft(fixture_result())
    selected = draft.result.selection.plans[1].plan_id
    runtime = CapturingRuntime()
    app = create_app(runtime_factory=lambda: runtime,
                     multi_stop_workspace_factory=lambda: workspace)
    with TestClient(app) as client:
        response = client.post("/api/chat/stream" if stream else "/api/chat", json={
            "message": "改成少步行", "multi_stop_draft_id": str(draft.draft_id),
            "multi_stop_plan_id": selected,
        })
        assert response.status_code == 200
        context = runtime.drafts[0]
        assert context.result.selection.selected_plan_id == selected
        assert len(context.result.request.stops) == 3
        value = chat_input("改成少步行", context)
        assert value["messages"][-1].content == "改成少步行"
        assert has_current_multi_stop(value["messages"])
        assert context.model_dump_json() in value["messages"][-2].content
        assert client.post("/api/chat", json={
            "message": "修改", "multi_stop_draft_id": str(uuid4()),
        }).status_code == 404
        assert client.post("/api/chat", json={
            "message": "修改", "multi_stop_draft_id": str(draft.draft_id),
            "multi_stop_plan_id": "invented",
        }).status_code == 422
        assert len(runtime.drafts) == 1
    assert await workspace.get_draft(draft.draft_id) == draft
