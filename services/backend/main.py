import os
import tempfile
import time
from typing import Optional

import requests
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from services.editor.exporter import EditorAssetStore, export_project
from services.pipeline.job_manager import VideoJob, VideoJobManager

app = FastAPI(title="Local AI Media Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SD_HOST = os.getenv("SD_HOST", "http://sd-host:9000")
SD_STARTUP_TIMEOUT_SECONDS = int(os.getenv("SD_STARTUP_TIMEOUT_SECONDS", "300"))
SD_HEALTH_TIMEOUT_SECONDS = int(os.getenv("SD_HEALTH_TIMEOUT_SECONDS", "2"))
SD_GENERATE_TIMEOUT_SECONDS = int(os.getenv("SD_GENERATE_TIMEOUT_SECONDS", "600"))
JOB_OUTPUT_DIR = os.getenv("JOB_OUTPUT_DIR", "/app/runtime/jobs")
EDITOR_RUNTIME_DIR = os.getenv("EDITOR_RUNTIME_DIR", "/app/runtime/editor")

session = requests.Session()
retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"],
)
adapter = HTTPAdapter(max_retries=retry)
session.mount("http://", adapter)
session.mount("https://", adapter)

job_manager = VideoJobManager(
    session=session,
    sd_host=SD_HOST,
    output_root=JOB_OUTPUT_DIR,
    sd_generate_timeout_seconds=SD_GENERATE_TIMEOUT_SECONDS,
)
editor_store = EditorAssetStore(runtime_dir=EDITOR_RUNTIME_DIR)


class GenerateImageRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=800)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Prompt is required")
        return text


class CreateVideoJobRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=800)
    narration: Optional[str] = Field(default=None, max_length=1200)
    use_replicate: bool = True
    use_elevenlabs: bool = True

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Prompt is required")
        return text

    @field_validator("narration")
    @classmethod
    def validate_narration(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        text = value.strip()
        if not text:
            return None
        return text


class TimelineClip(BaseModel):
    id: str = Field(..., min_length=1, max_length=120)
    asset_id: Optional[str] = None
    start: float = 0.0
    in_point: float = 0.0
    duration: float = 3.0
    transition_in: float = 0.0
    transition_out: float = 0.0
    volume: float = 1.0
    text: Optional[str] = None
    end: Optional[float] = None
    font_size: int = 42
    color: str = "white"
    x: int = 40
    y: Optional[int] = None


class TimelineTrack(BaseModel):
    id: str = Field(..., min_length=1, max_length=120)
    clips: list[TimelineClip] = Field(default_factory=list)


class ProjectExportRequest(BaseModel):
    width: int = 1280
    height: int = 720
    fps: int = 24
    duration: Optional[float] = None
    bg_color: str = "black"
    video_tracks: list[TimelineTrack] = Field(default_factory=list)
    audio_tracks: list[TimelineTrack] = Field(default_factory=list)
    text_tracks: list[TimelineTrack] = Field(default_factory=list)


def wait_for_sd_on_demand(timeout: int = SD_STARTUP_TIMEOUT_SECONDS) -> None:
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = session.get(f"{SD_HOST}/health", timeout=SD_HEALTH_TIMEOUT_SECONDS)
            if r.status_code == 200:
                body = r.json()
                if body.get("status") == "ok":
                    return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise HTTPException(
        status_code=503,
        detail="Image service is still starting. Try again in a minute.",
    )


def serialize_job(job: VideoJob) -> dict:
    data = {
        "job_id": job.job_id,
        "prompt": job.prompt,
        "status": job.status,
        "progress": job.progress,
        "stage": job.stage,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "error": job.error,
        "video_provider": job.video_provider,
        "audio_provider": job.audio_provider,
        "download_url": None,
    }
    if job.status == "completed" and job.output_path:
        data["download_url"] = f"/jobs/{job.job_id}/download"
    return data


@app.on_event("shutdown")
def shutdown_event() -> None:
    job_manager.stop()


@app.get("/health")
def health() -> dict:
    queue_size = job_manager.queue.qsize()
    assets_count = len(editor_store.list_assets())
    return {
        "status": "backend-ok",
        "sd_host": SD_HOST,
        "queued_jobs": queue_size,
        "editor_assets": assets_count,
    }


@app.post("/generate-image")
def generate_image(data: GenerateImageRequest) -> dict:
    wait_for_sd_on_demand()

    try:
        r = session.post(
            f"{SD_HOST}/generate",
            json={"prompt": data.prompt},
            timeout=SD_GENERATE_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
        payload = r.json()
        image_b64 = payload.get("image_base64")
        if not image_b64:
            raise HTTPException(status_code=502, detail="Image service returned no image data")
        return {"image_url": f"data:image/png;base64,{image_b64}"}
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Image generation timed out")
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else 502
        raise HTTPException(status_code=502, detail=f"Image service error: {status}")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Image service unavailable: {str(e)}")


@app.post("/jobs/video")
def create_video_job(data: CreateVideoJobRequest) -> dict:
    wait_for_sd_on_demand()
    job = job_manager.submit(
        prompt=data.prompt,
        narration=data.narration,
        use_replicate=data.use_replicate,
        use_elevenlabs=data.use_elevenlabs,
    )
    return {
        "job_id": job.job_id,
        "status": job.status,
        "poll_url": f"/jobs/{job.job_id}",
        "download_url": f"/jobs/{job.job_id}/download",
    }


@app.get("/jobs")
def list_jobs(limit: int = 25) -> dict:
    limit = max(1, min(limit, 100))
    jobs = [serialize_job(job) for job in job_manager.list_recent(limit=limit)]
    return {"jobs": jobs}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return serialize_job(job)


@app.get("/jobs/{job_id}/download")
def download_job(job_id: str) -> FileResponse:
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed" or not job.output_path:
        raise HTTPException(status_code=409, detail="Job is not completed yet")
    if not os.path.exists(job.output_path):
        raise HTTPException(status_code=404, detail="Output file missing")
    return FileResponse(
        path=job.output_path,
        media_type="video/mp4",
        filename=f"{job_id}.mp4",
    )


@app.post("/editor/assets/upload")
async def upload_editor_asset(file: UploadFile = File(...)) -> dict:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    suffix = os.path.splitext(file.filename)[1][:10]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        tmp.write(data)

    try:
        return editor_store.add_asset(file.filename, tmp_path)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.get("/editor/assets")
def list_editor_assets() -> dict:
    return {"assets": editor_store.list_assets()}


@app.get("/editor/assets/{asset_id}/download")
def download_editor_asset(asset_id: str) -> FileResponse:
    asset = editor_store.get_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    path = asset.get("path")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Asset file missing")
    filename = asset.get("original_name") or f"{asset_id}.bin"
    return FileResponse(path=path, media_type="application/octet-stream", filename=filename)


@app.post("/editor/export")
def export_editor_project(data: ProjectExportRequest) -> dict:
    payload = data.model_dump()
    try:
        result = export_project(payload, editor_store)
        editor_store.add_export_record(
            export_id=result.export_id,
            output_path=result.output_path,
            duration=result.duration,
            request_payload=payload,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "export_id": result.export_id,
        "duration": result.duration,
        "download_url": f"/editor/exports/{result.export_id}/download",
    }


@app.get("/editor/exports/{export_id}/download")
def download_editor_export(export_id: str) -> FileResponse:
    path = editor_store.get_export_path(export_id)
    if not path:
        raise HTTPException(status_code=404, detail="Export not found")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Export file missing")
    return FileResponse(
        path=path,
        media_type="video/mp4",
        filename=f"{export_id}.mp4",
    )
