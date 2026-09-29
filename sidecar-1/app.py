import os, uuid, httpx
from fastapi import FastAPI
app = FastAPI(title="Sidecar 1")
@app.get("/health")
async def health(): return {"status":"ok"}
@app.post("/invoke")
async def invoke(payload: dict):
    data = {"payload": payload, "workflow_id": payload.get("workflow_id", str(uuid.uuid4()))}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{os.getenv('CONTAINER_MANAGER_URL')}/agents/agent-1/invoke", json=data)
        response.raise_for_status()
        return response.json()
