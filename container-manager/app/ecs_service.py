import asyncio
import os
import uuid

class EcsService:
    """Small adapter: mock tasks locally, boto3 RunTask in AWS deployments."""
    def __init__(self, mock=True):
        self.mock = mock
        self.tasks = {}
        self.endpoints = {}

    async def run_task(self, agent_type: str, overrides: dict) -> str:
        if not self.mock:
            import boto3
            ecs = boto3.client("ecs", region_name=os.getenv("AWS_REGION", "ap-south-1"))
            subnets = [value.strip() for value in os.getenv("ECS_PRIVATE_SUBNETS", "").split(",") if value.strip()]
            response = await asyncio.to_thread(
                ecs.run_task,
                cluster=os.getenv("ECS_CLUSTER", "mastro-demo-cluster"),
                taskDefinition=os.environ[f"{agent_type.upper().replace('-', '_')}_TASK_DEFINITION"],
                capacityProviderStrategy=[{
                    "capacityProvider": os.getenv("ECS_CAPACITY_PROVIDER", "mastro-demo-ec2"),
                    "weight": 1,
                }],
                count=1,
                networkConfiguration={"awsvpcConfiguration": {
                    "subnets": subnets,
                    "securityGroups": [os.environ["ECS_TASK_SECURITY_GROUP"]],
                    "assignPublicIp": "DISABLED",
                }},
                overrides={"containerOverrides": [{
                    "name": agent_type,
                    "environment": [{"name": key, "value": str(value)} for key, value in overrides.items()],
                }]},
            )
            failures = response.get("failures", [])
            if failures:
                raise RuntimeError(f"ECS RunTask failed: {failures}")
            task_arn = response["tasks"][0]["taskArn"]
            for _ in range(30):
                details = await asyncio.to_thread(
                    ecs.describe_tasks,
                    cluster=os.getenv("ECS_CLUSTER", "mastro-demo-cluster"),
                    tasks=[task_arn],
                )
                task = details.get("tasks", [{}])[0]
                if task.get("lastStatus") == "RUNNING":
                    eni = next(
                        (detail["value"] for attachment in task.get("attachments", [])
                         for detail in attachment.get("details", [])
                         if detail.get("name") == "networkInterfaceId"),
                        None,
                    )
                    if eni:
                        network = await asyncio.to_thread(
                            boto3.client("ec2", region_name=os.getenv("AWS_REGION", "ap-south-1"))
                            .describe_network_interfaces,
                            NetworkInterfaceIds=[eni],
                        )
                        self.endpoints[task_arn] = (
                            network["NetworkInterfaces"][0]["PrivateIpAddress"]
                        )
                        break
                await asyncio.sleep(2)
            if task_arn not in self.endpoints:
                raise RuntimeError(f"ECS task did not become reachable: {task_arn}")
            return task_arn
        await asyncio.sleep(0)
        task_id = f"mock-{agent_type}-{uuid.uuid4().hex[:8]}"
        self.tasks[task_id] = {"agent_type": agent_type, "overrides": overrides, "status": "RUNNING"}
        return task_id

    async def invoke_task(self, task_id: str, agent_type: str, payload: dict) -> dict:
        """Execute the local mock task; AWS mode is replaced by the ECS adapter."""
        await asyncio.sleep(3)
        if agent_type == "agent-1":
            result = {"agent": "agent-1", "message": "Agent 1 completed", "value": 42}
            result["handoff"] = await self.invoke_task(task_id, "agent-2", {**payload, **result})
            return result
        return {"agent": "agent-2", "message": "Agent 2 completed", "result": "workflow complete"}

    def endpoint(self, task_id: str) -> str | None:
        return self.endpoints.get(task_id)

    async def describe_task(self, task_id: str) -> dict:
        return self.tasks.get(task_id, {"status": "STOPPED"})

    async def stop_task(self, task_id: str):
        if task_id in self.tasks:
            self.tasks[task_id]["status"] = "STOPPED"
