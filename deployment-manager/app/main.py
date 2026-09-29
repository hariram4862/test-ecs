from fastapi import FastAPI, HTTPException
from .models import DeployRequest
from .repository import DeploymentRepository
from .deployment_service import DeploymentService
app = FastAPI(title="Mastro Deployment Manager")
repo, service = DeploymentRepository(), None
@app.on_event("startup")
async def startup():
    global service
    service = DeploymentService(repo)
@app.get("/health")
async def health(): return {"status":"ok"}
@app.post("/deploy", status_code=202)
async def deploy(request: DeployRequest): return await service.create(request)
@app.get("/deployments")
async def deployments(): return repo.all()
@app.get("/deployments/{deployment_id}")
async def deployment(deployment_id: str):
    item = repo.get(deployment_id)
    if not item: raise HTTPException(404, "deployment not found")
    return item
@app.post("/deployments/{deployment_id}/destroy")
async def destroy(deployment_id: str):
    item = repo.get(deployment_id)
    if not item: raise HTTPException(404, "deployment not found")
    item["state"] = "DESTROYED"; return item
