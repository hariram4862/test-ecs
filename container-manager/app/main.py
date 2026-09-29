import asyncio
import os
import httpx
from fastapi import FastAPI, HTTPException
from .config import MOCK_ECS, MAX_AGENTS
from .models import Registration, Invocation
from .agent_registry import AgentRegistry
from .ecs_service import EcsService
from .scheduler import Scheduler
from .scaling import Scaling

app = FastAPI(title="Mastro Container Manager")
registry, ecs = AgentRegistry(), EcsService(MOCK_ECS)
scheduler, scaling = Scheduler(registry, ecs, MAX_AGENTS), None
results: dict[str, dict] = {}

@app.on_event("startup")
async def warm_pool():
    global scaling
    scaling = Scaling(registry, scheduler)
    # Agents are intentionally not started here. They are ECS RunTask workloads.

@app.get("/health")
async def health(): return {"status": "ok", "agents": len(registry.agents)}

@app.post("/agents/register")
async def register(item: Registration): return await registry.register(item)

@app.post("/agents/{agent_id}/heartbeat")
async def heartbeat(agent_id: str):
    result = await registry.heartbeat(agent_id)
    if not result: raise HTTPException(404, "agent not registered")
    return result

@app.get("/agents")
async def agents(): return list(registry.agents.values())

@app.post("/agents/{agent_type}/invoke")
async def invoke(agent_type: str, request: Invocation):
    key = request.idempotency_key or request.run_id or request.workflow_id
    if key and key in results:
        return results[key]
    agent = await registry.choose(agent_type)
    if not agent:
        if len(registry.agents) >= MAX_AGENTS:
            raise HTTPException(503, "agent pool saturated")
        task_id = await scheduler.launch(agent_type, request.run_id)
        agent = await registry.choose(agent_type)
        if not agent:
            raise HTTPException(503, f"{agent_type} task {task_id} is not ready")
    try:
        if MOCK_ECS:
            result = await ecs.invoke_task(agent.task_id or agent.agent_id, agent_type, request.payload)
        else:
            address = ecs.endpoint(agent.task_id or agent.agent_id)
            url = f"http://{address}:8000" if address else None
            if not url:
                raise HTTPException(503, f"{agent_type} task has no reachable endpoint")
            async with httpx.AsyncClient(timeout=40) as client:
                response = await client.post(f"{url}/work", json=request.payload)
                response.raise_for_status()
                result = response.json()
        if key:
            results[key] = result
        return result
    finally:
        await registry.release(agent.agent_id)
