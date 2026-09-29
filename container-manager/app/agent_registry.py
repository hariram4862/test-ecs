import asyncio
from .models import Agent, Registration

class AgentRegistry:
    def __init__(self):
        self.agents: dict[str, Agent] = {}
        self.lock = asyncio.Lock()

    async def register(self, item: Registration) -> Agent:
        async with self.lock:
            agent = Agent(**item.model_dump())
            self.agents[item.agent_id] = agent
            return agent

    async def choose(self, agent_type: str) -> Agent | None:
        async with self.lock:
            candidates = [a for a in self.agents.values()
                          if a.agent_type == agent_type and a.healthy and a.state == "READY"
                          and a.active < a.capacity]
            if not candidates:
                return None
            agent = min(candidates, key=lambda a: a.active)
            agent.active += 1
            return agent

    async def release(self, agent_id: str):
        async with self.lock:
            if agent_id in self.agents:
                self.agents[agent_id].active = max(0, self.agents[agent_id].active - 1)

    async def heartbeat(self, agent_id: str):
        async with self.lock:
            if agent_id not in self.agents:
                return None
            self.agents[agent_id].healthy = True
            return self.agents[agent_id]
