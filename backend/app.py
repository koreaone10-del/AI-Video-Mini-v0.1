import json
import os
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


APP_NAME = "AI-Video-Mini"

GITHUB_DISPATCH_TOKEN = os.getenv("GITHUB_DISPATCH_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "koreaone10-del")
GITHUB_REPO = os.getenv("GITHUB_REPO", "AI-Video-Mini-v0.1")
GITHUB_EVENT_TYPE = os.getenv("GITHUB_EVENT_TYPE", "ai_video_job")
GITHUB_API_URL = os.getenv(
    "GITHUB_API_URL",
    "https://api.github.com"
).rstrip("/")

RENDER_CALLBACK_SECRET = os.getenv(
    "RENDER_CALLBACK_SECRET",
    ""
)

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
VIDEO_DIR = BASE_DIR / "generated_videos"

VIDEO_DIR.mkdir(parents=True, exist_ok=True)

MAX_VIDEO_SIZE = 100 * 1024 * 1024

app = FastAPI(
    title=APP_NAME,
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS: dict[str, dict] = {}


class CreateJob(BaseModel):
    prompt: str = Field(
        ...,
        min_length=3,
        max_length=2000
    )

    aspect_ratio: str = "16:9"

    duration: int = Field(
        default=5,
        ge=1,
        le=5
    )


class WorkerCallback(BaseModel):
    status: str

    progress: int = Field(
        default=0,
        ge=0,
        le=100
    )

    stage: str = ""

    output_url: Optional[str] = None

    error: Optional[str] = None


def now():
    return time.time()


def clamp_progress(value):
    return max(0, min(100, int(value)))


def callback_authorized(request: Request):
    if not RENDER_CALLBACK_SECRET:
        return False

    return (
        request.headers.get(
            "X-Callback-Secret",
            ""
        )
        == RENDER_CALLBACK_SECRET
    )


def dispatch_to_github(job_id, payload):

    if not GITHUB_DISPATCH_TOKEN:
        raise RuntimeError(
            "GITHUB_DISPATCH_TOKEN is not configured"
        )

    url = (
        f"{GITHUB_API_URL}/repos/"
        f"{GITHUB_OWNER}/{GITHUB_REPO}/dispatches"
    )

    body = {
        "event_type": GITHUB_EVENT_TYPE,
        "client_payload": {
            "job_id": job_id,
            "prompt": payload["prompt"],
            "aspect_ratio": payload["aspect_ratio"],
            "duration": payload["duration"],
            "request": payload
        }
    }

    data = json.dumps(body).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": (
                f"Bearer {GITHUB_DISPATCH_TOKEN}"
            ),
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "AI-Video-Mini"
        }
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=30
        ) as response:

            if response.status != 204:
                body = response.read().decode(
                    "utf-8",
                    errors="replace"
                )

                raise RuntimeError(
                    f"GitHub dispatch returned "
                    f"HTTP {response.status}: {body[:500]}"
                )

    except urllib.error.HTTPError as exc:

        error_body = exc.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"GitHub dispatch failed HTTP "
            f"{exc.code}: {error_body[:500]}"
        ) from exc

    except urllib.error.URLError as exc:

        raise RuntimeError(
            f"GitHub dispatch network error: {exc}"
        ) from exc


@app.get("/health")
def health():

    return {
        "ok": True,
        "service": APP_NAME,
        "github_repository": GITHUB_REPO,
        "github_event_type": GITHUB_EVENT_TYPE,
        "github_dispatch_configured": bool(
            GITHUB_DISPATCH_TOKEN
        ),
        "callback_configured": bool(
            RENDER_CALLBACK_SECRET
        )
    }


@app.post("/api/jobs")
def create_job(payload: CreateJob):

    job_id = uuid.uuid4().hex[:12]

    created_at = now()

    job = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "stage": "queued",
        "created_at": created_at,
        "updated_at": created_at,
        "output_url": None,
        "error": None,
        "request": payload.model_dump()
    }

    JOBS[job_id] = job

    try:

        dispatch_to_github(
            job_id,
            payload.model_dump()
        )

        job["status"] = "dispatched"
        job["progress"] = 2
        job["stage"] = "github_actions"
        job["updated_at"] = now()

    except Exception as exc:

        job["status"] = "failed"
        job["progress"] = 100
        job["stage"] = "dispatch_failed"
        job["error"] = str(exc)
        job["updated_at"] = now()

        raise HTTPException(
            status_code=502,
            detail={
                "message": "Unable to start GitHub Worker",
                "job_id": job_id,
                "error": str(exc)
            }
        )

    return job


@app.get("/api/jobs")
def list_jobs():

    jobs = list(JOBS.values())

    jobs.sort(
        key=lambda item: item.get(
            "created_at",
            0
        ),
        reverse=True
    )

    return jobs[:30]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):

    job = JOBS.get(job_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    return job


@app.post("/api/worker/jobs/{job_id}/upload")
async def upload_worker_video(
    job_id: str,
    request: Request
):

    if not callback_authorized(request):
        raise HTTPException(
            status_code=401,
            detail="Invalid callback secret"
        )

    job = JOBS.get(job_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    content_type = request.headers.get(
        "Content-Type",
        ""
    ).lower()

    if "video/mp4" not in content_type:
        raise HTTPException(
            status_code=415,
            detail="Expected video/mp4"
        )

    output_path = VIDEO_DIR / f"{job_id}.mp4"

    total_size = 0

    try:

        with output_path.open("wb") as video_file:

            async for chunk in request.stream():

                if not chunk:
                    continue

                total_size += len(chunk)

                if total_size > MAX_VIDEO_SIZE:

                    video_file.close()

                    if output_path.exists():
                        output_path.unlink()

                    raise HTTPException(
                        status_code=413,
                        detail="Video file is too large"
                    )

                video_file.write(chunk)

    except HTTPException:
        raise

    except Exception as exc:

        if output_path.exists():
            output_path.unlink()

        raise HTTPException(
            status_code=500,
            detail=f"Video upload failed: {exc}"
        )

    if total_size <= 0:

        if output_path.exists():
            output_path.unlink()

        raise HTTPException(
            status_code=400,
            detail="Uploaded video is empty"
        )

    output_url = (
        f"/api/jobs/{job_id}/video"
    )

    job["output_url"] = output_url
    job["updated_at"] = now()
    job["stage"] = "video_uploaded"

    return {
        "ok": True,
        "job_id": job_id,
        "size": total_size,
        "output_url": output_url
    }


@app.get("/api/jobs/{job_id}/video")
def get_job_video(job_id: str):

    video_path = VIDEO_DIR / f"{job_id}.mp4"

    if not video_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Video not found"
        )

    return FileResponse(
        path=str(video_path),
        media_type="video/mp4",
        filename=f"{job_id}.mp4"
    )


@app.post("/api/worker/jobs/{job_id}/callback")
def worker_callback(
    job_id: str,
    update: WorkerCallback,
    request: Request
):

    if not callback_authorized(request):
        raise HTTPException(
            status_code=401,
            detail="Invalid callback secret"
        )

    job = JOBS.get(job_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    job["status"] = update.status

    job["progress"] = clamp_progress(
        update.progress
    )

    job["stage"] = update.stage

    job["updated_at"] = now()

    if update.output_url:
        job["output_url"] = update.output_url

    if update.error:
        job["error"] = update.error

    if update.status == "completed":
        job["progress"] = 100
        job["stage"] = (
            update.stage or "completed"
        )

    if update.status == "failed":
        job["progress"] = 100
        job["stage"] = (
            update.stage or "failed"
        )

    return {
        "ok": True,
        "job_id": job_id,
        "status": job["status"],
        "progress": job["progress"],
        "stage": job["stage"],
        "output_url": job.get("output_url")
    }


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):

    if job_id not in JOBS:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    video_path = VIDEO_DIR / f"{job_id}.mp4"

    if video_path.exists():
        try:
            video_path.unlink()
        except Exception:
            pass

    del JOBS[job_id]

    return {
        "ok": True,
        "job_id": job_id
    }


if FRONTEND_DIR.exists():

    assets_dir = FRONTEND_DIR / "assets"

    if assets_dir.exists():

        app.mount(
            "/assets",
            StaticFiles(
                directory=str(assets_dir)
            ),
            name="assets"
        )

    app.mount(
        "/",
        StaticFiles(
            directory=str(FRONTEND_DIR),
            html=True
        ),
        name="frontend"
    )
