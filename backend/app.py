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


# ============================================================
# APP CONFIG
# ============================================================

APP_NAME = "AI-Video-Mini"

GITHUB_DISPATCH_TOKEN = os.getenv(
    "GITHUB_DISPATCH_TOKEN",
    ""
)

GITHUB_OWNER = os.getenv(
    "GITHUB_OWNER",
    "koreaone10-del"
)

GITHUB_REPO = os.getenv(
    "GITHUB_REPO",
    "AI-Video-Mini-v0.1"
)

GITHUB_EVENT_TYPE = os.getenv(
    "GITHUB_EVENT_TYPE",
    "ai_video_job"
)

GITHUB_API_URL = os.getenv(
    "GITHUB_API_URL",
    "https://api.github.com"
).rstrip("/")

RENDER_CALLBACK_SECRET = os.getenv(
    "RENDER_CALLBACK_SECRET",
    ""
)


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

FRONTEND_DIR = BASE_DIR / "frontend"

VIDEO_DIR = BASE_DIR / "generated_videos"

VIDEO_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# LIMITS
# ============================================================

MAX_VIDEO_SIZE = 100 * 1024 * 1024


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version="1.1.0"
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# IN-MEMORY JOB STORE
# ============================================================
#
# IMPORTANT:
#
# Render Free can restart the Web Service.
# Therefore this dictionary can disappear.
#
# The recovery system below recreates missing jobs
# automatically when the Worker calls back.
#
# No external database/service is used.
#
# ============================================================

JOBS: dict[str, dict] = {}


# ============================================================
# REQUEST MODELS
# ============================================================

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

    # Optional metadata.
    #
    # Current worker does not have to send these.
    # They are here so a future callback can restore
    # the original request after a Render restart.

    prompt: Optional[str] = None

    aspect_ratio: Optional[str] = None

    duration: Optional[int] = None


# ============================================================
# HELPERS
# ============================================================

def now():
    return time.time()


def clamp_progress(value):
    return max(
        0,
        min(
            100,
            int(value)
        )
    )


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


# ============================================================
# JOB RECOVERY
# ============================================================
#
# This is the main fix.
#
# If Render restarts, JOBS becomes empty.
#
# Previously:
#
#     JOBS.get(job_id)
#     -> None
#     -> HTTP 404
#
# Now:
#
#     missing job
#     -> recreate placeholder
#     -> continue Worker operation
#
# ============================================================

def recover_job(
    job_id: str,
    prompt: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    duration: Optional[int] = None
):

    existing = JOBS.get(job_id)

    if existing:

        request_data = existing.get(
            "request"
        )

        if not isinstance(
            request_data,
            dict
        ):
            request_data = {}

            existing["request"] = request_data

        if prompt:
            request_data["prompt"] = prompt

        if aspect_ratio:
            request_data["aspect_ratio"] = aspect_ratio

        if duration is not None:
            request_data["duration"] = duration

        existing["updated_at"] = now()

        return existing

    recovered_at = now()

    recovered_request = {
        "prompt": prompt or "",
        "aspect_ratio": (
            aspect_ratio
            or "16:9"
        ),
        "duration": (
            duration
            if duration is not None
            else 5
        )
    }

    job = {
        "job_id": job_id,

        "status": "processing",

        "progress": 0,

        "stage": "recovered_after_restart",

        "created_at": recovered_at,

        "updated_at": recovered_at,

        "output_url": None,

        "error": None,

        "request": recovered_request,

        "recovered": True,

        "recovered_at": recovered_at
    }

    JOBS[job_id] = job

    return job


def get_or_recover_job(job_id: str):

    job = JOBS.get(job_id)

    if job:
        return job

    return recover_job(job_id)


# ============================================================
# GITHUB DISPATCH
# ============================================================

def dispatch_to_github(
    job_id,
    payload
):

    if not GITHUB_DISPATCH_TOKEN:

        raise RuntimeError(
            "GITHUB_DISPATCH_TOKEN is not configured"
        )

    url = (
        f"{GITHUB_API_URL}/repos/"
        f"{GITHUB_OWNER}/"
        f"{GITHUB_REPO}/dispatches"
    )

    body = {
        "event_type": GITHUB_EVENT_TYPE,

        "client_payload": {
            "job_id": job_id,

            "prompt": payload[
                "prompt"
            ],

            "aspect_ratio": payload[
                "aspect_ratio"
            ],

            "duration": payload[
                "duration"
            ],

            "request": payload
        }
    }

    data = json.dumps(
        body
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept":
                "application/vnd.github+json",

            "Authorization":
                f"Bearer {GITHUB_DISPATCH_TOKEN}",

            "X-GitHub-Api-Version":
                "2022-11-28",

            "Content-Type":
                "application/json",

            "User-Agent":
                "AI-Video-Mini"
        }
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=30
        ) as response:

            if response.status != 204:

                response_body = (
                    response
                    .read()
                    .decode(
                        "utf-8",
                        errors="replace"
                    )
                )

                raise RuntimeError(
                    "GitHub dispatch returned "
                    f"HTTP {response.status}: "
                    f"{response_body[:500]}"
                )

    except urllib.error.HTTPError as exc:

        error_body = (
            exc
            .read()
            .decode(
                "utf-8",
                errors="replace"
            )
        )

        raise RuntimeError(
            "GitHub dispatch failed HTTP "
            f"{exc.code}: "
            f"{error_body[:500]}"
        ) from exc

    except urllib.error.URLError as exc:

        raise RuntimeError(
            "GitHub dispatch network error: "
            f"{exc}"
        ) from exc


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "ok": True,

        "service":
            APP_NAME,

        "github_repository":
            GITHUB_REPO,

        "github_event_type":
            GITHUB_EVENT_TYPE,

        "github_dispatch_configured":
            bool(
                GITHUB_DISPATCH_TOKEN
            ),

        "callback_configured":
            bool(
                RENDER_CALLBACK_SECRET
            ),

        "jobs_in_memory":
            len(JOBS)
    }


# ============================================================
# CREATE JOB
# ============================================================

@app.post("/api/jobs")
def create_job(
    payload: CreateJob
):

    job_id = uuid.uuid4().hex[:12]

    created_at = now()

    request_data = (
        payload.model_dump()
    )

    job = {
        "job_id":
            job_id,

        "status":
            "queued",

        "progress":
            0,

        "stage":
            "queued",

        "created_at":
            created_at,

        "updated_at":
            created_at,

        "output_url":
            None,

        "error":
            None,

        "request":
            request_data,

        "recovered":
            False
    }

    JOBS[job_id] = job

    try:

        dispatch_to_github(
            job_id,
            request_data
        )

        job["status"] = (
            "dispatched"
        )

        job["progress"] = 2

        job["stage"] = (
            "github_actions"
        )

        job["updated_at"] = now()

    except Exception as exc:

        job["status"] = (
            "failed"
        )

        job["progress"] = 100

        job["stage"] = (
            "dispatch_failed"
        )

        job["error"] = str(exc)

        job["updated_at"] = now()

        raise HTTPException(
            status_code=502,

            detail={
                "message":
                    "Unable to start "
                    "GitHub Worker",

                "job_id":
                    job_id,

                "error":
                    str(exc)
            }
        )

    return job


# ============================================================
# LIST JOBS
# ============================================================

@app.get("/api/jobs")
def list_jobs():

    jobs = list(
        JOBS.values()
    )

    jobs.sort(
        key=lambda item:
            item.get(
                "created_at",
                0
            ),

        reverse=True
    )

    return jobs[:30]


# ============================================================
# GET JOB
# ============================================================
#
# IMPORTANT:
# If Render restarted and frontend polls an old job,
# do not immediately return 404.
#
# Recreate the job and let the Worker continue updating it.
#
# ============================================================

@app.get("/api/jobs/{job_id}")
def get_job(
    job_id: str
):

    job = get_or_recover_job(
        job_id
    )

    return job


# ============================================================
# WORKER VIDEO UPLOAD
# ============================================================

@app.post(
    "/api/worker/jobs/{job_id}/upload"
)
async def upload_worker_video(
    job_id: str,
    request: Request
):

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    if not callback_authorized(
        request
    ):

        raise HTTPException(
            status_code=401,
            detail="Invalid callback secret"
        )

    # --------------------------------------------------------
    # RECOVER JOB IF RENDER RESTARTED
    # --------------------------------------------------------

    job = get_or_recover_job(
        job_id
    )

    # --------------------------------------------------------
    # CONTENT TYPE
    # --------------------------------------------------------

    content_type = (
        request
        .headers
        .get(
            "Content-Type",
            ""
        )
        .lower()
    )

    if "video/mp4" not in content_type:

        raise HTTPException(
            status_code=415,
            detail="Expected video/mp4"
        )

    # --------------------------------------------------------
    # OUTPUT PATH
    # --------------------------------------------------------

    output_path = (
        VIDEO_DIR
        / f"{job_id}.mp4"
    )

    total_size = 0

    try:

        with output_path.open(
            "wb"
        ) as video_file:

            async for chunk in request.stream():

                if not chunk:
                    continue

                total_size += len(
                    chunk
                )

                # ------------------------------------------------
                # MAX SIZE PROTECTION
                # ------------------------------------------------

                if (
                    total_size
                    > MAX_VIDEO_SIZE
                ):

                    video_file.close()

                    if output_path.exists():

                        output_path.unlink()

                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "Video file "
                            "is too large"
                        )
                    )

                video_file.write(
                    chunk
                )

    except HTTPException:

        raise

    except Exception as exc:

        if output_path.exists():

            try:
                output_path.unlink()
            except Exception:
                pass

        raise HTTPException(
            status_code=500,
            detail=(
                "Video upload failed: "
                f"{exc}"
            )
        )

    # --------------------------------------------------------
    # EMPTY FILE
    # --------------------------------------------------------

    if total_size <= 0:

        if output_path.exists():

            try:
                output_path.unlink()
            except Exception:
                pass

        raise HTTPException(
            status_code=400,
            detail="Uploaded video is empty"
        )

    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    output_url = (
        f"/api/jobs/"
        f"{job_id}/video"
    )

    job["output_url"] = (
        output_url
    )

    job["updated_at"] = now()

    job["stage"] = (
        "video_uploaded"
    )

    # Keep processing state unless
    # the Worker already marked it completed.

    if job.get("status") not in (
        "completed",
        "failed"
    ):

        job["status"] = (
            "processing"
        )

    return {
        "ok": True,

        "job_id":
            job_id,

        "size":
            total_size,

        "output_url":
            output_url
    }


# ============================================================
# SERVE VIDEO
# ============================================================

@app.get(
    "/api/jobs/{job_id}/video"
)
def get_job_video(
    job_id: str
):

    video_path = (
        VIDEO_DIR
        / f"{job_id}.mp4"
    )

    if not video_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Video not found"
        )

    return FileResponse(
        path=str(
            video_path
        ),

        media_type="video/mp4",

        filename=(
            f"{job_id}.mp4"
        )
    )


# ============================================================
# WORKER CALLBACK
# ============================================================

@app.post(
    "/api/worker/jobs/{job_id}/callback"
)
def worker_callback(
    job_id: str,
    update: WorkerCallback,
    request: Request
):

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    if not callback_authorized(
        request
    ):

        raise HTTPException(
            status_code=401,
            detail="Invalid callback secret"
        )

    # --------------------------------------------------------
    # RECOVER JOB
    # --------------------------------------------------------
    #
    # This is the critical fix for:
    #
    #     {"detail":"Job not found"}
    #
    # after Render restart.
    #
    # --------------------------------------------------------

    job = recover_job(
        job_id,

        prompt=update.prompt,

        aspect_ratio=(
            update.aspect_ratio
        ),

        duration=(
            update.duration
        )
    )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    job["status"] = (
        update.status
    )

    # --------------------------------------------------------
    # PROGRESS
    # --------------------------------------------------------

    job["progress"] = (
        clamp_progress(
            update.progress
        )
    )

    # --------------------------------------------------------
    # STAGE
    # --------------------------------------------------------

    job["stage"] = (
        update.stage
    )

    # --------------------------------------------------------
    # TIMESTAMP
    # --------------------------------------------------------

    job["updated_at"] = now()

    # --------------------------------------------------------
    # OUTPUT URL
    # --------------------------------------------------------

    if update.output_url:

        job["output_url"] = (
            update.output_url
        )

    # --------------------------------------------------------
    # ERROR
    # --------------------------------------------------------

    if update.error:

        job["error"] = (
            update.error
        )

    # --------------------------------------------------------
    # COMPLETED
    # --------------------------------------------------------

    if update.status == "completed":

        job["progress"] = 100

        job["stage"] = (
            update.stage
            or "completed"
        )

    # --------------------------------------------------------
    # FAILED
    # --------------------------------------------------------

    if update.status == "failed":

        job["progress"] = 100

        job["stage"] = (
            update.stage
            or "failed"
        )

    # --------------------------------------------------------
    # RESPONSE
    # --------------------------------------------------------

    return {
        "ok": True,

        "job_id":
            job_id,

        "status":
            job["status"],

        "progress":
            job["progress"],

        "stage":
            job["stage"],

        "output_url":
            job.get(
                "output_url"
            )
    }


# ============================================================
# DELETE JOB
# ============================================================

@app.delete(
    "/api/jobs/{job_id}"
)
def delete_job(
    job_id: str
):

    job = JOBS.get(
        job_id
    )

    if not job:

        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    video_path = (
        VIDEO_DIR
        / f"{job_id}.mp4"
    )

    if video_path.exists():

        try:

            video_path.unlink()

        except Exception:

            pass

    del JOBS[job_id]

    return {
        "ok": True,

        "job_id":
            job_id
    }


# ============================================================
# FRONTEND
# ============================================================

if FRONTEND_DIR.exists():

    assets_dir = (
        FRONTEND_DIR
        / "assets"
    )

    if assets_dir.exists():

        app.mount(
            "/assets",

            StaticFiles(
                directory=str(
                    assets_dir
                )
            ),

            name="assets"
        )

    app.mount(
        "/",

        StaticFiles(
            directory=str(
                FRONTEND_DIR
            ),

            html=True
        ),

        name="frontend"
    )
