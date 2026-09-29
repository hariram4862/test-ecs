class HealthMonitor:
    def __init__(self, registry):
        self.registry = registry

    async def mark_unhealthy(self, agent_id: str):
        if agent_id in self.registry.agents:
            self.registry.agents[agent_id].healthy = False
