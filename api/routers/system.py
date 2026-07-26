"""
System Router.

Handles system health endpoints and live score WebSocket connections.
Strictly delegates logic to system_service.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from api.services import system_service
from api.services.system_service import manager

router = APIRouter(tags=["System & WebSockets"])


@router.websocket("/ws/live-scores")
async def websocket_live_scores(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, wait for client to disconnect
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@router.get("/api/health")
def health_check():
    return system_service.health_check_service()
