"""FastAPI application entrypoint.

Run with: uvicorn main_api:app --host 0.0.0.0 --port 8000 --workers 4
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

import jwt
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_auth import router as auth_router
from app.api.routes_clusters import router as clusters_router
from app.api.routes_design import router as design_router
from app.api.routes_incidents import router as incidents_router
from app.api.routes_playbooks import router as playbooks_router
from app.api.routes_projects import router as projects_router
from app.api.routes_rules import router as rules_router
from app.api.routes_tools import router as tools_router
from app.api.routes_webhooks import router as webhooks_router
from app.api.routes_workflows import router as workflows_router
from app.config import get_settings
from app.dependencies import cleanup_dependencies, get_redis, init_dependencies
from app.services.broadcast import CHANNEL_ALL, CHANNEL_INCIDENTS, CHANNEL_WORKFLOWS

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_dependencies()
    yield
    await cleanup_dependencies()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AutoMend API",
        description="AI-powered incident response platform",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(design_router, prefix="/api/design", tags=["design"])
    app.include_router(incidents_router, prefix="/api/incidents", tags=["incidents"])
    app.include_router(rules_router, prefix="/api/rules", tags=["rules"])
    app.include_router(playbooks_router, prefix="/api/playbooks", tags=["playbooks"])
    app.include_router(projects_router, prefix="/api/projects", tags=["projects"])
    app.include_router(webhooks_router, prefix="/api/webhooks", tags=["webhooks"])
    app.include_router(workflows_router, prefix="/api/workflows", tags=["workflows"])
    app.include_router(tools_router, prefix="/api/tools", tags=["tools"])
    app.include_router(clusters_router, prefix="/api/clusters", tags=["clusters"])
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.websocket("/api/ws/incidents")
    async def ws_incidents(
        websocket: WebSocket,
        token: str = Query(..., description="JWT access token"),
        channel: str = Query("all", description="Filter: all, incidents, workflows"),
    ):
        """WebSocket for real-time incident and workflow events.

        Authenticate via `?token=<jwt>` query parameter. Subscribe to a
        Redis Pub/Sub channel and stream events to the client as JSON.
        """
        # Authenticate
        try:
            settings = get_settings()
            jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        except Exception:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
            return

        # Select channel
        channel_map = {
            "all": CHANNEL_ALL,
            "incidents": CHANNEL_INCIDENTS,
            "workflows": CHANNEL_WORKFLOWS,
        }
        target_channel = channel_map.get(channel, CHANNEL_ALL)

        redis = await get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(target_channel)

        await websocket.accept()
        try:
            # Send a ready message
            await websocket.send_json({"event_type": "ready", "channel": channel})

            while True:
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    await websocket.send_json({"event_type": "heartbeat"})
                    continue

                if message is None:
                    continue

                data_raw = message.get("data")
                if isinstance(data_raw, bytes):
                    data_raw = data_raw.decode()
                try:
                    await websocket.send_json(json.loads(data_raw))
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Dropping malformed pubsub message: %r", data_raw)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("WebSocket error")
        finally:
            try:
                await pubsub.unsubscribe(target_channel)
                await pubsub.aclose()
            except Exception:
                pass

    return app


app = create_app()
