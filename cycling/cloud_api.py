"""Authenticated FastAPI adapter for the Vercel deployment."""

import os
from datetime import UTC, date, datetime
from typing import Annotated, Iterator

import jwt
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from cycling.models import (
    ActivityContext,
    ActivityRequest,
    AthleteParameters,
    ComparisonRequest,
    CurveRequest,
    DurabilityRequest,
    LoadRequest,
    PeriodHRDistributionRequest,
    PeriodPowerCurveRequest,
    ProgressRequest,
    SetContextRequest,
    StreamRequest,
    ToolResult,
)
from cycling.postgres_store import PostgresStore
from cycling.service import CyclingService

bearer = HTTPBearer(auto_error=False)


class UploadProcessRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    object_key: str = Field(min_length=1, max_length=512)


def authenticated_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    secret = os.environ.get("INTERNAL_API_SECRET", "")
    if len(secret.encode()) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        )
    try:
        claims = jwt.decode(
            credentials.credentials,
            secret,
            algorithms=["HS256"],
            issuer="cycling-nextjs",
            audience="cycling-fastapi",
            options={"require": ["sub", "iat", "exp"]},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session"
        ) from exc
    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session"
        )
    return user_id


def request_store(
    user_id: Annotated[str, Depends(authenticated_user)],
) -> Iterator[PostgresStore]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured",
        )
    try:
        store = PostgresStore(database_url, user_id)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User is not active"
        ) from exc
    try:
        yield store
    finally:
        store.close()


StoreDep = Annotated[PostgresStore, Depends(request_store)]

app = FastAPI(title="Cycling analytics API", version="1")


@app.get("/api/v1/health")
def health():
    # Public liveness only: never return tenant/catalog status here.
    return {"status": "ok"}


@app.get("/api/v1/activities", response_model=ToolResult)
def activities(store: StoreDep):
    return CyclingService(store).list_activities()


@app.get("/api/v1/status", response_model=ToolResult)
def status_report(store: StoreDep):
    return CyclingService(store).status()


@app.post("/api/v1/activity", response_model=ToolResult)
def activity(request: ActivityRequest, store: StoreDep):
    return CyclingService(store).get_activity(request)


@app.post("/api/v1/activity/analyze", response_model=ToolResult)
def analyze_activity(request: ActivityRequest, store: StoreDep):
    return CyclingService(store).analyze_activity(request)


@app.post("/api/v1/stream", response_model=ToolResult)
def stream(request: StreamRequest, store: StoreDep):
    return CyclingService(store).stream(request)


@app.post("/api/v1/power-curve", response_model=ToolResult)
def power_curve(request: CurveRequest, store: StoreDep):
    return CyclingService(store).power_curve(request)


@app.post("/api/v1/power-curves", response_model=ToolResult)
def period_power_curves(request: PeriodPowerCurveRequest, store: StoreDep):
    return CyclingService(store).period_power_curve(request)


@app.post("/api/v1/hr-distribution", response_model=ToolResult)
def hr_distribution(request: ActivityRequest, store: StoreDep):
    return CyclingService(store).hr_distribution(request)


@app.post("/api/v1/period-hr-distributions", response_model=ToolResult)
def period_hr_distributions(request: PeriodHRDistributionRequest, store: StoreDep):
    return CyclingService(store).period_hr_distribution(request)


@app.post("/api/v1/durability", response_model=ToolResult)
def durability(request: DurabilityRequest, store: StoreDep):
    return CyclingService(store).durability(request)


@app.post("/api/v1/aerobic-durability", response_model=ToolResult)
def aerobic_durability(request: ActivityRequest, store: StoreDep):
    return CyclingService(store).aerobic_durability(request)


@app.post("/api/v1/drift", response_model=ToolResult)
def drift(request: ActivityRequest, store: StoreDep):
    return CyclingService(store).drift(request)


@app.post("/api/v1/thresholds", response_model=ToolResult)
def thresholds(request: ActivityRequest, store: StoreDep):
    return CyclingService(store).thresholds(request)


@app.post("/api/v1/load", response_model=ToolResult)
def load(request: LoadRequest, store: StoreDep):
    return CyclingService(store).load(request)


@app.post("/api/v1/compare", response_model=ToolResult)
def compare(request: ComparisonRequest, store: StoreDep):
    return CyclingService(store).compare(request)


@app.post("/api/v1/progress", response_model=ToolResult)
def progress(request: ProgressRequest, store: StoreDep):
    return CyclingService(store).progress(request)


@app.get("/api/v1/weekly-cycling-training", response_model=ToolResult)
def weekly_cycling_training(
    store: StoreDep,
    end: Annotated[date, Query(default_factory=lambda: datetime.now(UTC).date())],
    weeks: Annotated[int, Query(ge=1, le=104)] = 12,
):
    return CyclingService(store).weekly_cycling_training(end, weeks)


@app.get("/api/v1/power-hr-zone-mismatch", response_model=ToolResult)
def power_hr_zone_mismatch(
    store: StoreDep,
    period: str = Query(default="90d"),
    environment: str = Query(default="outdoor"),
    parameter_mode: str = Query(default="historical"),
):
    return CyclingService(store).power_hr_zone_mismatch(
        period, environment, parameter_mode
    )


@app.post("/api/v1/activity/context", response_model=ToolResult)
def set_context(request: SetContextRequest, store: StoreDep):
    context = ActivityContext(rpe=request.rpe, modality=request.modality)
    return CyclingService(store).set_context(request.activity_id, context)


@app.get("/api/v1/parameters", response_model=ToolResult)
def list_parameters(store: StoreDep):
    return CyclingService(store).list_parameters()


@app.post("/api/v1/parameters", response_model=ToolResult)
def add_parameters(parameters: AthleteParameters, store: StoreDep):
    return CyclingService(store).add_parameters(parameters)


@app.post("/api/v1/uploads/process")
def process_upload(request: UploadProcessRequest, store: StoreDep):
    return store.ingest_blob(request.filename, request.object_key)


@app.post("/api/v1/uploads")
async def upload_activity(store: StoreDep, file: Annotated[UploadFile, File()]):
    filename = file.filename or ""
    limit = int(os.environ.get("MAX_UPLOAD_BYTES", "25000000"))
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(
            status_code=413, detail="Upload exceeds configured size limit"
        )
    if not content:
        raise HTTPException(status_code=422, detail="Upload is empty")
    return store.ingest_upload(filename, content)


@app.exception_handler(KeyError)
async def not_found(_, exc: KeyError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def invalid_request(_, exc: ValueError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=422, content={"detail": str(exc)})
