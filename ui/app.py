import os, httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
app = FastAPI(title="Mastro UI")

@app.get("/")
async def index(): return FileResponse("index.html")

@app.get("/health")
async def health(): return {"status": "ok"}

@app.post("/workflow/start")
async def start(payload: dict = {}):
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.post(f"{os.getenv('SIDECAR_1_URL')}/invoke", json=payload)
        response.raise_for_status()
        return {"status":"SUCCESS","result":response.json()}
