import asyncio, os, httpx
from fastapi import FastAPI
app = FastAPI(title="Agent 2")
@app.on_event("startup")
async def register():
    base = os.getenv("CONTAINER_MANAGER_URL")
    if base:
        async with httpx.AsyncClient() as client:
            for _ in range(5):
                try:
                    await client.post(f"{base}/agents/register", json={"agent_id": os.getenv("AGENT_ID","agent-2"), "agent_type":"agent-2", "capacity":5})
                    break
                except httpx.HTTPError: await asyncio.sleep(1)
@app.get("/health")
async def health(): return {"status":"ok"}
@app.get("/capacity")
async def capacity(): return {"active":0,"capacity":5}
@app.post("/work")
async def work(payload: dict):
    await asyncio.sleep(3)
    return {"agent":"agent-2","message":"Agent 2 completed","result":"workflow complete"}
