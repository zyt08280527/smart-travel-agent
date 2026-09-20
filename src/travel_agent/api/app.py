from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from travel_agent.api.runtime import (
    AgentRuntime,
    AgentRuntimeError,
    AgentRuntimeNotStartedError,
    ConversationNotFoundError,
    PendingApprovalNotFoundError,
)
from travel_agent.api.schemas import (
    AgentResponse,
    ApprovalRequest,
    ChatRequest,
    HealthResponse,
)
from travel_agent.config import get_api_settings
from travel_agent.domain.conversation import (
    ConversationDetail,
    ConversationSummary,
)
from travel_agent.domain.itinerary import SavedItinerary
from travel_agent.services.itinerary import (
    ItineraryNotFoundError,
    ItineraryService,
    ItineraryServiceError,
)

RuntimeFactory = Callable[[], AgentRuntime]
ItineraryServiceFactory = Callable[[], ItineraryService]

def get_runtime(request: Request) -> AgentRuntime:
    """Return the long-lived Agent runtime created during application startup."""
    runtime = getattr(request.app.state, "agent_runtime", None)
    if runtime is None:
        raise AgentRuntimeNotStartedError("Agent运行时尚未启动")
    return cast(AgentRuntime, runtime)


def create_app(
    runtime_factory: RuntimeFactory = AgentRuntime,
    cors_allowed_origins: Sequence[str] | None = None,
    itinerary_service_factory: ItineraryServiceFactory = ItineraryService,
) -> FastAPI:
    """Create the FastAPI application with a lifespan-managed Agent runtime."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = runtime_factory()
        await runtime.start()
        app.state.agent_runtime = runtime
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(
        title="Smart Travel Agent API",
        version="0.1.0",
        lifespan=lifespan,
    )
    allowed_origins = (
        list(cors_allowed_origins)
        if cors_allowed_origins is not None
        else get_api_settings().cors_allowed_origins
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(PendingApprovalNotFoundError)
    async def pending_approval_not_found(
        _request: Request,
        exc: PendingApprovalNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConversationNotFoundError)
    async def conversation_not_found(
        _request: Request,
        exc: ConversationNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(AgentRuntimeNotStartedError)
    async def runtime_not_started(
        _request: Request,
        exc: AgentRuntimeNotStartedError,
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(AgentRuntimeError)
    async def runtime_conflict(
        _request: Request,
        exc: AgentRuntimeError,
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health", response_model=HealthResponse)
    async def health(
        runtime: Annotated[AgentRuntime, Depends(get_runtime)],
    ) -> HealthResponse:
        return HealthResponse(status="ok", tools=runtime.tool_names)

    @app.post("/api/chat", response_model=AgentResponse)
    async def chat(
        request: ChatRequest,
        runtime: Annotated[AgentRuntime, Depends(get_runtime)],
    ) -> AgentResponse:
        return await runtime.chat(request.message, request.thread_id)

    @app.get(
        "/api/conversations",
        response_model=list[ConversationSummary],
    )
    async def list_conversations(
        runtime: Annotated[AgentRuntime, Depends(get_runtime)],
        limit: int = 50,
    ) -> list[ConversationSummary]:
        return await runtime.list_conversations(limit)

    @app.get("/api/itineraries", response_model=list[SavedItinerary])
    async def list_itineraries(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> list[SavedItinerary]:
        try:
            return await itinerary_service_factory().list(limit)
        except ItineraryServiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.delete("/api/itineraries/{itinerary_id}", status_code=204)
    async def delete_itinerary(itinerary_id: str) -> Response:
        try:
            await itinerary_service_factory().delete(itinerary_id)
        except ItineraryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ItineraryServiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(status_code=204)

    @app.get(
        "/api/conversations/{thread_id}",
        response_model=ConversationDetail,
    )
    async def get_conversation(
        thread_id: UUID,
        runtime: Annotated[AgentRuntime, Depends(get_runtime)],
    ) -> ConversationDetail:
        return await runtime.get_conversation(thread_id)

    @app.delete(
        "/api/conversations/{thread_id}",
        status_code=204,
    )
    async def delete_conversation(
        thread_id: UUID,
        runtime: Annotated[AgentRuntime, Depends(get_runtime)],
    ) -> Response:
        await runtime.delete_conversation(thread_id)
        return Response(status_code=204)

    @app.post("/api/chat/stream")
    async def chat_stream(
        request: ChatRequest,
        runtime: Annotated[AgentRuntime, Depends(get_runtime)],
    ) -> StreamingResponse:
        async def event_lines() -> AsyncIterator[str]:
            async for event in runtime.stream_chat(
                request.message,
                request.thread_id,
            ):
                payload = event.model_dump_json(
                    exclude_none=True,
                    exclude_defaults=True,
                )
                yield f"{payload}\n"

        return StreamingResponse(
            event_lines(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post(
        "/api/threads/{thread_id}/approval",
        response_model=AgentResponse,
    )
    async def decide(
        thread_id: UUID,
        request: ApprovalRequest,
        runtime: Annotated[AgentRuntime, Depends(get_runtime)],
    ) -> AgentResponse:
        return await runtime.decide(thread_id, request)

    return app


app = create_app()
