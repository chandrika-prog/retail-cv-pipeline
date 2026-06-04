from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, List
import asyncio, json

class ConnectionManager:
    def __init__(self):
        self.active: Dict[str, List[WebSocket]] = {}

    async def connect(self, store_id: str, ws: WebSocket):
        await ws.accept()
        self.active.setdefault(store_id, []).append(ws)

    def disconnect(self, store_id: str, ws: WebSocket):
        connections = self.active.get(store_id, [])
        if ws in connections:
            connections.remove(ws)
        if not connections and store_id in self.active:
            del self.active[store_id]

    async def broadcast(self, store_id: str, data: dict):
        for ws in list(self.active.get(store_id, [])):
            try:
                await ws.send_json(data)
            except:
                self.disconnect(store_id, ws)

manager = ConnectionManager()
