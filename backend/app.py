from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import traceback
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .wan_runner import generate, OUTPUT_DIR

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(title="AI-Video-Mini")
executor = ThreadPoolExecutor(max_workers=1)
jobs = {}

class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)

def run_job(job_id, prompt):
    jobs[job_id]["status"] = "generating"
    try:
        real_id, _ = generate(prompt)
        jobs[job_id].update(
            status="done",
            file=f"/api/video/{real_id}",
            error=None,
        )
    except Exception as exc:
        jobs[job_id].update(
            status="error",
            error=f"{exc}\n\n{traceback.format_exc()}",
        )

@app.get("/api/health")
def health():
    return {"ok": True}

@app.post("/api/generate")
def create_job(payload: GenerateRequest):
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"status": "queued", "file": None, "error": None}
    executor.submit(run_job, job_id, payload.prompt.strip())
    return {"job_id": job_id, "status": "queued"}

@app.get("/api/status/{job_id}")
def status(job_id):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, **jobs[job_id]}

@app.get("/api/video/{video_id}")
def video(video_id):
    path = OUTPUT_DIR / f"{video_id}.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(path, media_type="video/mp4", filename=path.name)

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
