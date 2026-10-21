"""Application assembly (plan V2.0 sections 9.3, 9.7).

``/v1`` is the current contract; ``/v0`` keeps the cluster-label route as a deprecated alias that
still names the run it came from. Every request carries one trace id end to end - reused from an
inbound ``X-Trace-Id`` when a caller supplies a well-formed one, so the id can propagate through the
state machine and the workflow runner - and produces exactly one masked access log line.
"""

from __future__ import annotations

import re
from time import perf_counter

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from .deps import ApiState, DeploymentCapabilities, StateDep, get_claims, open_session
from .errors import register_handlers
from .observability import emit, new_trace_id
from .routes_annotations import router as annotations_router
from .routes_compliance import router as compliance_router
from .routes_data import router as data_router
from .routes_exports import router as exports_router
from .routes_findings import read_evidence_by_alias
from .routes_findings import router as findings_router
from .routes_knowledge import router as knowledge_router
from .routes_orchestration import router as orchestration_router
from .routes_runs import router as runs_router
from .routes_tools import router as tools_router

TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
SUCCESSOR_ROUTE = "/v1/projects/{project_id}/evidence"


def trace_id_for(request: Request) -> str:
    supplied = request.headers.get("X-Trace-Id", "")
    return supplied if TRACE_ID_PATTERN.fullmatch(supplied) else new_trace_id()


def create_app(state: ApiState) -> FastAPI:
    app = FastAPI(title="BioAdapt API", version="1.0", docs_url=None, redoc_url=None)
    app.state.api = state
    register_handlers(app)

    @app.middleware("http")
    async def traced(request: Request, call_next):
        request.state.trace_id = trace_id_for(request)
        started = perf_counter()
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        emit(
            state.log_sink,
            "info",
            timestamp=state.clock(),
            trace_id=request.state.trace_id,
            action=f"{request.method} {request.url.path}",
            status=str(response.status_code),
            latency_ms=round((perf_counter() - started) * 1000, 3),
        )
        return response

    @app.get("/v1/deployment")
    def deployment(state: StateDep) -> DeploymentCapabilities:
        """Anonymous, non-sensitive declarations only; ``configured`` is not a health check."""
        return state.deployment

    for router in (
        data_router,
        runs_router,
        orchestration_router,
        annotations_router,
        findings_router,
        knowledge_router,
        tools_router,
        exports_router,
        compliance_router,
    ):
        app.include_router(router, prefix="/v1")

    @app.get("/v0/projects/{project_id}/clusters/{cluster_id}/evidence", deprecated=True)
    def legacy_cluster_evidence(
        project_id: str, cluster_id: str, request: Request, run_id: str = Query(...)
    ) -> JSONResponse:
        """Section 9.3: the label-only route survives as an alias, but the caller must say which run."""
        session = open_session(state, get_claims(request, state), project_id, request.state.trace_id)
        body = read_evidence_by_alias(session=session, candidate_id=None, cluster_id=cluster_id, run_id=run_id)
        headers = {
            "Deprecation": "true",
            "Link": f'<{SUCCESSOR_ROUTE.format(project_id=project_id)}>; rel="successor-version"',
        }
        return JSONResponse(status_code=200, content=body, headers=headers)

    return app
