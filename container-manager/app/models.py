from pydantic import BaseModel, Field

class Registration(BaseModel):
    agent_id: str
    agent_type: str
    task_id: str | None = None
    capacity: int = 5

class Invocation(BaseModel):
    payload: dict = Field(default_factory=dict)
    workflow_id: str | None = None
    run_id: str | None = None
    idempotency_key: str | None = None

class Agent(BaseModel):
    agent_id: str
    agent_type: str
    task_id: str | None = None
    capacity: int = 5
    active: int = 0
    healthy: bool = True
    state: str = "READY"
