"""FastAPI application for the simulation service.

Endpoints
---------
GET  /health    service + ngspice availability (used by docker-compose healthcheck and the API)
POST /simulate  validate a graph, generate a netlist, run `ngspice -b`, parse the .op table
GET  /mapping   node -> netlist mapping table (docs/debugging)
GET  /          tiny index so the service is browsable

This is the ONLY process in the stack that executes ngspice. The Express API is the only caller.
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import get_settings
from .engine import evaluate_graph, ngspice_health
from .errors import Codes, SimulationError
from .mapping import mapping_table
from .schemas import ErrorBody, ErrorResponse, HealthResponse, SimulateRequest, SimulateResponse

STARTED_AT = time.time()

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("lng.sim")

app = FastAPI(
    title="Live Node Graph Engine — simulation service",
    version="1.0.0",
    description="Validates node graphs, generates netlists and executes them with ngspice in batch mode.",
)


@app.get("/", include_in_schema=False)
def index() -> dict[str, object]:
    return {
        "service": settings.service_name,
        "endpoints": ["/health", "/simulate", "/mapping", "/docs"],
        "note": "only this service executes ngspice",
    }


@app.get("/health", response_model=HealthResponse)
def health() -> JSONResponse:
    version, error = ngspice_health(settings)
    payload = HealthResponse(
        status="ok" if version else "degraded",
        ngspice={
            "available": version is not None,
            "version": version,
            "binary": settings.ngspice_bin,
            "error": error,
        },
        config=settings.public(),
        uptimeSeconds=round(time.time() - STARTED_AT, 3),
    )
    status_code = 200 if version else 503
    return JSONResponse(status_code=status_code, content=payload.model_dump())


@app.get("/mapping")
def mapping() -> dict[str, object]:
    return {"mapping": mapping_table(), "ground": "0", "note": "1 V == 1 unit of node value"}


@app.post("/simulate", response_model=SimulateResponse)
def simulate(request: SimulateRequest) -> JSONResponse:
    logger.info(
        "simulate request revision=%s nodes=%d edges=%d",
        request.revision,
        len(request.graph.nodes),
        len(request.graph.edges),
    )
    try:
        response = evaluate_graph(request.graph, revision=request.revision, settings=settings)
    except SimulationError as error:
        logger.error("simulation failed code=%s message=%s", error.code, error.message)
        return JSONResponse(
            status_code=502,
            content=ErrorResponse(
                error=ErrorBody(code=error.code, message=error.message, details={"detail": error.detail} if error.detail else None)
            ).model_dump(),
        )
    except Exception:  # pragma: no cover - last-resort guard, keeps the service responsive
        logger.exception("unhandled simulation error")
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error=ErrorBody(code=Codes.NGSPICE_ERROR, message="unexpected simulation failure")
            ).model_dump(),
        )
    return JSONResponse(status_code=200, content=response.model_dump())


@app.exception_handler(Exception)
async def unhandled(request: Request, error: Exception) -> JSONResponse:  # pragma: no cover
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error=ErrorBody(code=Codes.SIMULATION_SERVICE_UNAVAILABLE, message="internal simulation service error")
        ).model_dump(),
    )
