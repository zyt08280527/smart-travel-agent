import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager, suppress
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
from travel_agent.domain.attraction import AttractionLookup
from travel_agent.domain.conversation import (
    ConversationDetail,
    ConversationSummary,
)
from travel_agent.domain.departure_watch import WatchReadInput
from travel_agent.domain.discovery import (
    DiscoveryChooseInput,
    DiscoveryChooseResponse,
    DiscoveryHistoryInfo,
    DiscoveryRecommendInput,
    DiscoveryRecommendResponse,
    DiscoveryRefineRequest,
    DiscoveryRefineResponse,
    DiscoveryRequest,
    DiscoverySelectionInput,
)
from travel_agent.domain.itinerary import SavedItinerary
from travel_agent.domain.multi_stop import (
    MultiStopDraft,
    MultiStopRequest,
    SavedMultiStopJourney,
    SaveMultiStopRequest,
    SelectMultiStopRequest,
)
from travel_agent.domain.multi_stop_weather import MultiStopWeatherReport
from travel_agent.domain.place import DeviceLocationInput, Place, PlaceSearchResult
from travel_agent.services.amap_city import AmapCityServiceError
from travel_agent.services.amap_coordinate import AmapCoordinateServiceError
from travel_agent.services.attraction import AttractionService
from travel_agent.services.conversation import (
    ConversationRepository,
    ConversationRepositoryError,
    ConversationRevisionConflict,
)
from travel_agent.services.departure_watch import DepartureWatch
from travel_agent.services.device_location import DeviceLocationService
from travel_agent.services.discovery import DiscoveryService
from travel_agent.services.discovery_conversation import DiscoveryConversationService
from travel_agent.services.discovery_history import DiscoveryHistoryMissing, DiscoveryHistoryService
from travel_agent.services.itinerary import (
    ItineraryNotFoundError,
    ItineraryService,
    ItineraryServiceError,
)
from travel_agent.services.multi_stop_planning import MultiStopPlanningService
from travel_agent.services.multi_stop_weather import MultiStopWeatherService
from travel_agent.services.multi_stop_workspace import (
    MultiStopDraftNotFound,
    MultiStopWorkspace,
    MultiStopWorkspaceError,
)
from travel_agent.services.place import PlaceService, PlaceServiceError
from travel_agent.services.single_departure_watch import SingleDepartureChecker, SingleWatchInput

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
    multi_stop_service_factory: Callable[[], MultiStopPlanningService] = MultiStopPlanningService,
    multi_stop_workspace_factory: Callable[[], MultiStopWorkspace] = MultiStopWorkspace,
    place_service_factory: Callable[[], PlaceService] = PlaceService,
    multi_stop_weather_factory: Callable[[], MultiStopWeatherService] = MultiStopWeatherService,
    conversation_repository_factory: Callable[[], ConversationRepository] = ConversationRepository,
    single_watch_checker_factory: Callable[[], SingleDepartureChecker] | None = None,
    device_location_factory: Callable[[], DeviceLocationService] = DeviceLocationService,
) -> FastAPI:
    """Create the FastAPI application with a lifespan-managed Agent runtime."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = runtime_factory()
        await runtime.start()
        app.state.agent_runtime = runtime
        worker = asyncio.create_task(watch_service().run())
        try:
            yield
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
            await runtime.stop()

    app = FastAPI(
        title="Smart Travel Agent API",
        version="0.1.0",
        lifespan=lifespan,
    )

    def watch_service():
        weather = multi_stop_weather_factory()
        return DepartureWatch(
            multi_stop_workspace_factory(),
            multi_stop_service_factory(),
            weather,
            single_watch_checker_factory()
            if single_watch_checker_factory
            else SingleDepartureChecker(weather),
        )

    @app.post("/api/itineraries/{itinerary_id}/watch")
    async def enable_single_departure_watch(itinerary_id: str, request: SingleWatchInput):
        try:
            saved = await itinerary_service_factory().get(itinerary_id)
            return await watch_service().enable_single(saved, request.departure_at)
        except ItineraryNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ItineraryServiceError as exc:
            raise HTTPException(503, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/departure-watches")
    async def list_departure_watches():
        return await watch_service().list()

    @app.get('/api/departure-watches/{watch_id}')
    async def departure_watch_detail(watch_id: UUID):
        try:
            return await watch_service().detail(watch_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post('/api/departure-watches/{watch_id}/read', status_code=204)
    async def read_departure_watch(watch_id: UUID, request: WatchReadInput):
        try:
            await watch_service().mark_read(watch_id, request.notification_token)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/multi-stop/saved/{saved_id}/watch")
    async def enable_departure_watch(saved_id: UUID):
        try:
            return await watch_service().enable(saved_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/departure-watches/{watch_id}", status_code=204)
    async def cancel_departure_watch(watch_id: UUID):
        await watch_service().cancel(watch_id)

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

    @app.exception_handler(MultiStopDraftNotFound)
    async def multi_stop_missing(_request: Request, exc: MultiStopDraftNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(MultiStopWorkspaceError)
    async def multi_stop_storage_error(
        _request: Request, exc: MultiStopWorkspaceError
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(ConversationRevisionConflict)
    async def discovery_conflict(_request: Request, exc: ConversationRevisionConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(DiscoveryHistoryMissing)
    async def discovery_missing(_request: Request, exc: DiscoveryHistoryMissing):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConversationRepositoryError)
    async def discovery_storage_error(_request: Request, exc: ConversationRepositoryError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    def discovery_history_service():
        planner = multi_stop_service_factory()
        return DiscoveryHistoryService(
            conversation_repository_factory(),
            DiscoveryService(planner, multi_stop_weather_factory()),
            multi_stop_workspace_factory(),
            planner,
        )

    @app.post(
        "/api/discovery/recommend",
        response_model=DiscoveryRecommendResponse,
        response_model_exclude_unset=True,
    )
    async def discover(request: DiscoveryRecommendInput):
        try:
            if request.thread_id:
                return await discovery_history_service().start(request)
            criteria = DiscoveryRequest.model_validate(
                request.model_dump(
                    exclude={
                        "thread_id",
                        "expected_revision",
                    }
                )
            )
            result = await DiscoveryService(
                multi_stop_service_factory(),
                multi_stop_weather_factory(),
            ).recommend(criteria)
            return DiscoveryRecommendResponse(**result.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/discovery/refine",
        response_model=DiscoveryRefineResponse,
        response_model_exclude_unset=True,
    )
    async def refine_discovery(request: DiscoveryRefineRequest):
        try:
            if request.thread_id:
                return await discovery_history_service().refine(request)
            discovery = DiscoveryService(
                multi_stop_service_factory(),
                multi_stop_weather_factory(),
            )
            return await DiscoveryConversationService(discovery).refine(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/conversations/{thread_id}/discovery/choose",
        response_model=DiscoveryChooseResponse,
        response_model_exclude_unset=True,
    )
    async def choose_discovery(thread_id: UUID, request: DiscoveryChooseInput):
        try:
            return await discovery_history_service().choose(thread_id, request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/api/conversations/{thread_id}/discovery/selection",
        response_model=DiscoveryHistoryInfo,
        response_model_exclude_unset=True,
    )
    async def discovery_selection(thread_id: UUID, request: DiscoverySelectionInput):
        try:
            return await discovery_history_service().selection(thread_id, request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/attractions/lookup", response_model=AttractionLookup)
    async def lookup_attraction(place: Place):
        return AttractionService().lookup(place)

    @app.post('/api/places/device-location', response_model=Place)
    async def resolve_device_location(position: DeviceLocationInput):
        try:
            return await device_location_factory().resolve(position)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (AmapCityServiceError, AmapCoordinateServiceError) as exc:
            raise HTTPException(503, '位置地址解析暂时不可用，请重试或手动选择出发点') from exc

    @app.get("/api/places", response_model=PlaceSearchResult)
    async def search_places(query: Annotated[str, Query(min_length=1, max_length=200)]):
        try:
            return await place_service_factory().search_places(query, limit=5)
        except PlaceServiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/multi-stop/plan", response_model=MultiStopDraft)
    async def plan_multi_stop(request: MultiStopRequest):
        try:
            result = await multi_stop_service_factory().plan(request)
            return await multi_stop_workspace_factory().create_draft(result)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/multi-stop/drafts/{draft_id}", response_model=MultiStopDraft)
    async def get_multi_stop_draft(draft_id: UUID):
        return await multi_stop_workspace_factory().get_draft(draft_id)

    @app.post("/api/multi-stop/drafts/{draft_id}/select", response_model=MultiStopDraft)
    async def select_multi_stop(draft_id: UUID, request: SelectMultiStopRequest):
        try:
            return await multi_stop_workspace_factory().select(draft_id, request.candidate_ids)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/multi-stop/drafts/{draft_id}/weather", response_model=MultiStopWeatherReport)
    async def multi_stop_weather(draft_id: UUID, request: SaveMultiStopRequest):
        try:
            draft = await multi_stop_workspace_factory().get_plan_draft(
                draft_id,
                request.selected_plan_id,
            )
            return await multi_stop_weather_factory().report(draft, request.selected_plan_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/multi-stop/drafts/{draft_id}/save", response_model=SavedMultiStopJourney)
    async def save_multi_stop(draft_id: UUID, request: SaveMultiStopRequest):
        try:
            return await multi_stop_workspace_factory().save(draft_id, request.selected_plan_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/multi-stop/itineraries", response_model=list[SavedMultiStopJourney])
    async def list_saved_multi_stop(limit: Annotated[int, Query(ge=1, le=100)] = 20):
        return await multi_stop_workspace_factory().list_saved(limit)

    @app.get("/health", response_model=HealthResponse)
    async def health(
        runtime: Annotated[AgentRuntime, Depends(get_runtime)],
    ) -> HealthResponse:
        return HealthResponse(status="ok", tools=runtime.tool_names)

    async def chat_context(request: ChatRequest) -> dict:
        if not request.multi_stop_draft_id:
            if request.multi_stop_plan_id:
                raise HTTPException(status_code=422, detail="方案上下文需要对应草案")
            return {}
        workspace = multi_stop_workspace_factory()
        if request.multi_stop_plan_id:
            try:
                draft = await workspace.get_plan_draft(
                    request.multi_stop_draft_id, request.multi_stop_plan_id
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            draft = draft.model_copy(deep=True)
            draft.result.selection.selected_plan_id = request.multi_stop_plan_id
        else:
            draft = await workspace.get_draft(request.multi_stop_draft_id)
        return {"multi_stop_draft": draft}

    @app.post("/api/chat", response_model=AgentResponse)
    async def chat(
        request: ChatRequest,
        runtime: Annotated[AgentRuntime, Depends(get_runtime)],
    ) -> AgentResponse:
        context = await chat_context(request)
        return await runtime.chat(request.message, request.thread_id, **context)

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
            await watch_service().cancel_saved_single(itinerary_id)
            await itinerary_service_factory().delete(itinerary_id)
        except ItineraryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ItineraryServiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(status_code=204)

    @app.get(
        "/api/conversations/{thread_id}",
        response_model=ConversationDetail,
        response_model_exclude_unset=True,
    )
    async def get_conversation(
        thread_id: UUID,
        runtime: Annotated[AgentRuntime, Depends(get_runtime)],
    ) -> ConversationDetail:
        return await runtime.get_conversation(thread_id)

    @app.get("/api/conversations/{thread_id}/journey-versions")
    async def list_journey_versions(
        thread_id: UUID,
        runtime: Annotated[AgentRuntime, Depends(get_runtime)],
    ) -> list[dict]:
        return await runtime.list_journey_versions(thread_id)

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
        context = await chat_context(request)

        async def event_lines() -> AsyncIterator[str]:
            async for event in runtime.stream_chat(
                request.message,
                request.thread_id,
                **context,
            ):
                payload = event.model_dump_json(
                    exclude_none=True,
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
