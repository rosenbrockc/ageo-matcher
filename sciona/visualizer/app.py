"""FastAPI app factory for the visualizer."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from sciona.visualizer.cdg import router as cdg_router
from sciona.visualizer.dashboard import router as dashboard_router
from sciona.visualizer.isomorphisms import router as isomorphism_router
from sciona.visualizer.runner_api import router as runner_router
from sciona.visualizer.static import mount_static_assets


@asynccontextmanager
async def lifespan(app: FastAPI):
    from neo4j import AsyncGraphDatabase

    from sciona.config import AgeomConfig

    config = AgeomConfig()
    from sciona.telemetry import configure_dashboard_output

    configure_dashboard_output(config.telemetry_runs_dir)
    auth = (config.memgraph_user, config.memgraph_password) if config.memgraph_user else None
    driver = AsyncGraphDatabase.driver(config.memgraph_uri, auth=auth)
    app.state.driver = driver

    telem_drain = None
    telem_store = None
    if config.telemetry_backend != "file" and config.postgres_uri:
        try:
            from sciona.telemetry import configure_postgres_telemetry
            from sciona.telemetry_store import PostgresTelemetryStore, TelemetryDrain

            telem_store = PostgresTelemetryStore(config.postgres_uri)
            await telem_store.setup()
            telem_drain = TelemetryDrain(telem_store)
            configure_postgres_telemetry(telem_store, telem_drain)
            await telem_drain.start()
        except Exception:
            telem_drain = None
            telem_store = None

    try:
        yield
    finally:
        if telem_drain is not None:
            try:
                await telem_drain.stop()
            except Exception:
                pass
        if telem_store is not None:
            try:
                await telem_store.close()
            except Exception:
                pass
        await driver.close()


def create_app() -> FastAPI:
    app = FastAPI(title="AGEO CDG Visualizer", lifespan=lifespan)
    app.include_router(dashboard_router)
    app.include_router(cdg_router)
    app.include_router(isomorphism_router)
    app.include_router(runner_router)
    mount_static_assets(app)
    return app


app = create_app()
