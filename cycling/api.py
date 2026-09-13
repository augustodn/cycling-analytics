"""Loopback-only HTTP adapter. Run through `python -m cycling api`."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from cycling.models import (
    ActivityContext,
    ActivityRequest,
    AthleteParameters,
    ComparisonRequest,
    CurveRequest,
    DurabilityRequest,
    LoadRequest,
    PeriodPowerCurveRequest,
    SetContextRequest,
    StreamRequest,
    ToolResult,
)
from cycling.service import CyclingService
from cycling.storage import Store


def create_app(data_dir=".cycling"):
    @asynccontextmanager
    async def lifespan(app):
        with Store(data_dir) as store:
            app.state.service = CyclingService(store)
            yield

    app = FastAPI(title="Local cycling tools", lifespan=lifespan)

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(FileNotFoundError)
    async def missing_samples(request, exc):
        return JSONResponse(
            status_code=409,
            content={"detail": "Normalized data missing; run ingest to repair"},
        )

    # ponytail: synchronous local work on the event loop serializes the shared DB;
    # move to a worker queue/per-request connections if multiuser throughput is needed.
    @app.get("/activities", response_model=ToolResult)
    async def activities():
        return app.state.service.list_activities()

    @app.get("/status", response_model=ToolResult)
    async def status():
        return app.state.service.status()

    @app.get("/health", response_model=ToolResult)
    async def health():
        return app.state.service.status()

    @app.post("/activity", response_model=ToolResult)
    async def activity(request: ActivityRequest):
        return app.state.service.get_activity(request)

    @app.post("/activity/analyze", response_model=ToolResult)
    async def analyze(request: ActivityRequest):
        return app.state.service.analyze_activity(request)

    @app.post("/stream", response_model=ToolResult)
    async def stream(request: StreamRequest):
        return app.state.service.stream(request)

    @app.post("/power-curve", response_model=ToolResult)
    async def curve(request: CurveRequest):
        return app.state.service.power_curve(request)

    @app.post("/power-curves", response_model=ToolResult)
    async def period_power_curves(request: PeriodPowerCurveRequest):
        return app.state.service.period_power_curve(request)

    @app.post("/durability", response_model=ToolResult)
    async def durability(request: DurabilityRequest):
        return app.state.service.durability(request)

    @app.post("/drift", response_model=ToolResult)
    async def drift(request: ActivityRequest):
        return app.state.service.drift(request)

    @app.post("/thresholds", response_model=ToolResult)
    async def thresholds(request: ActivityRequest):
        return app.state.service.thresholds(request)

    @app.post("/load", response_model=ToolResult)
    async def load(request: LoadRequest):
        return app.state.service.load(request)

    @app.post("/compare", response_model=ToolResult)
    async def compare(request: ComparisonRequest):
        return app.state.service.compare(request)

    @app.post("/activity/context", response_model=ToolResult)
    async def set_context(request: SetContextRequest):
        return app.state.service.set_context(
            request.activity_id,
            ActivityContext(rpe=request.rpe, modality=request.modality),
        )

    @app.get("/parameters", response_model=ToolResult)
    async def list_parameters():
        return app.state.service.list_parameters()

    @app.post("/parameters", response_model=ToolResult)
    async def add_parameters(parameters: AthleteParameters):
        return app.state.service.add_parameters(parameters)

    return app
