import asyncio, os, httpx
from fastapi import FastAPI
app = FastAPI(title="Agent 1")
@app.on_event("startup")
async def register():
    base = os.getenv("CONTAINER_MANAGER_URL")
    if base:
        async with httpx.AsyncClient() as client:
            for _ in range(5):
                try:
                    await client.post(f"{base}/agents/register", json={"agent_id": os.getenv("AGENT_ID","agent-1"), "agent_type":"agent-1", "capacity":5})
                    break
                except httpx.HTTPError: await asyncio.sleep(1)
@app.get("/health")
async def health(): return {"status":"ok"}
@app.get("/capacity")
async def capacity(): return {"active":0,"capacity":5}
@app.post("/work")
async def work(payload: dict):
    await asyncio.sleep(3)
    result = {"agent":"agent-1","message":"Agent 1 completed","value":42}
    sidecar = os.getenv("SIDECAR_2_URL")
    if sidecar:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{sidecar}/invoke", json={**payload, **result})
            response.raise_for_status()
            result["handoff"] = response.json()
    return result
