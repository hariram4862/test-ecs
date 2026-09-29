import asyncio, uuid
from .services.deployer import AwsDeployer
class DeploymentService:
    def __init__(self, repo): self.repo, self.deployer = repo, AwsDeployer()
    async def create(self, request):
        record = {"id": str(uuid.uuid4()), "state":"QUEUED", **request.model_dump()}
        self.repo.save(record)
        asyncio.create_task(self._run(record))
        return record
    async def _run(self, record):
        try: await self.deployer.run(record)
        except Exception as exc: record.update(state="FAILED", error=str(exc))
