class Scaling:
    def __init__(self, registry, scheduler):
        self.registry, self.scheduler = registry, scheduler

    async def scale_if_needed(self, agent_type: str):
        await self.scheduler.ensure_warm(agent_type)
