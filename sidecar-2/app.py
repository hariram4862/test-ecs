import os, httpx
from fastapi import FastAPI
app = FastAPI(title="Sidecar 2")
@app.get("/health")
async def health(): return {"status":"ok"}
@app.post("/invoke")
async def invoke(payload: dict):
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{os.getenv('CONTAINER_MANAGER_URL')}/agents/agent-2/invoke",
                                     json={"payload": payload})
        response.raise_for_status()
        return response.json()
