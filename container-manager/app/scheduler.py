import asyncio

class Scheduler:
    def __init__(self, registry, ecs, max_agents):
        self.registry, self.ecs, self.max_agents = registry, ecs, max_agents

    async def ensure_warm(self, agent_type: str):
        return None

    async def launch(self, agent_type: str, run_id: str | None = None) -> str:
        task_id = await self.ecs.run_task(agent_type, {
            "AGENT_ID": f"{agent_type}-{run_id or 'run'}",
            "AGENT_TYPE": agent_type,
            "RUN_ID": run_id or "",
            "MAX_CONCURRENCY": "5",
        })
        from .models import Registration
        await self.registry.register(Registration(
            agent_id=f"{agent_type}-{run_id or 'run'}", agent_type=agent_type, task_id=task_id,
        ))
        return task_id
