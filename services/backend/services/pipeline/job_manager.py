import base64
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Dict, Optional

import requests


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _estimate_duration_seconds(text: str) -> int:
    words = max(1, len(text.split()))
    estimated = int(words * 0.45) + 2
    return max(6, min(20, estimated))


def _run_command(cmd: list[str], prefix: str) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        raise RuntimeError(f"{prefix}: {err[-600:]}")


@dataclass
class VideoJob:
    job_id: str
    prompt: str
    narration: str
    use_replicate: bool
    use_elevenlabs: bool
    status: str
    progress: int
    stage: str
    created_at: str
    updated_at: str
    error: Optional[str] = None
    output_path: Optional[str] = None
    video_provider: Optional[str] = None
    audio_provider: Optional[str] = None


class VideoJobManager:
    def __init__(
        self,
        session: requests.Session,
        sd_host: str,
        output_root: str,
        sd_generate_timeout_seconds: int = 600,
    ) -> None:
        self.session = session
        self.sd_host = sd_host
        self.output_root = output_root
        self.sd_generate_timeout_seconds = sd_generate_timeout_seconds

        self.jobs: Dict[str, VideoJob] = {}
        self.queue: Queue[str] = Queue()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

    def submit(self, prompt: str, narration: Optional[str], use_replicate: bool, use_elevenlabs: bool) -> VideoJob:
        job_id = uuid.uuid4().hex
        job = VideoJob(
            job_id=job_id,
            prompt=prompt,
            narration=(narration or prompt).strip(),
            use_replicate=use_replicate,
            use_elevenlabs=use_elevenlabs,
            status="queued",
            progress=0,
            stage="queued",
            created_at=_iso_now(),
            updated_at=_iso_now(),
        )
        with self.lock:
            self.jobs[job_id] = job
        self.queue.put(job_id)
        return job

    def get(self, job_id: str) -> Optional[VideoJob]:
        with self.lock:
            return self.jobs.get(job_id)

    def list_recent(self, limit: int = 25) -> list[VideoJob]:
        with self.lock:
            values = list(self.jobs.values())
        values.sort(key=lambda x: x.created_at, reverse=True)
        return values[:limit]

    def stop(self) -> None:
        self.stop_event.set()
        self.worker.join(timeout=2)

    def _update(self, job_id: str, **kwargs) -> None:
        with self.lock:
            job = self.jobs[job_id]
            for key, value in kwargs.items():
                setattr(job, key, value)
            job.updated_at = _iso_now()

    def _worker_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                job_id = self.queue.get(timeout=0.5)
            except Empty:
                continue
            try:
                self._run_job(job_id)
            except Exception as e:
                self._update(job_id, status="failed", progress=100, stage="failed", error=str(e))
            finally:
                self.queue.task_done()

    def _run_job(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            return
        duration_seconds = _estimate_duration_seconds(job.narration)

        job_dir = os.path.join(self.output_root, job_id)
        os.makedirs(job_dir, exist_ok=True)
        self._update(job_id, status="running", progress=5, stage="starting")

        video_path: Optional[str] = None
        video_provider = "local"
        if job.use_replicate:
            self._update(job_id, progress=20, stage="generating_video_replicate")
            try:
                video_path = self._generate_video_replicate(job.prompt, duration_seconds, job_dir)
                video_provider = "replicate"
            except Exception:
                video_path = None

        if video_path is None:
            self._update(job_id, progress=30, stage="generating_video_local")
            video_path = self._generate_video_local(job.prompt, duration_seconds, job_dir)
            video_provider = "local"

        self._update(job_id, progress=55, stage="generating_audio")
        audio_path, audio_provider = self._generate_audio(job.narration, duration_seconds, job_dir, job.use_elevenlabs)

        self._update(job_id, progress=80, stage="muxing")
        output_path = os.path.join(job_dir, "final.mp4")
        self._mux_video_and_audio(video_path, audio_path, output_path)

        self._update(
            job_id,
            status="completed",
            progress=100,
            stage="completed",
            output_path=output_path,
            video_provider=video_provider,
            audio_provider=audio_provider,
        )

    def _generate_video_replicate(self, prompt: str, duration_seconds: int, job_dir: str) -> str:
        token = os.getenv("REPLICATE_API_TOKEN", "").strip()
        version = os.getenv("REPLICATE_MODEL_VERSION", "").strip()
        if not token or not version:
            raise RuntimeError("Replicate not configured")

        headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "version": version,
            "input": {
                "prompt": prompt,
                "duration": duration_seconds,
            },
        }
        response = self.session.post(
            "https://api.replicate.com/v1/predictions",
            json=payload,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        prediction = response.json()
        poll_url = prediction.get("urls", {}).get("get")
        if not poll_url:
            raise RuntimeError("Replicate polling URL missing")

        start = time.time()
        while time.time() - start < 900:
            state = self.session.get(poll_url, headers=headers, timeout=30)
            state.raise_for_status()
            body = state.json()
            status = body.get("status")
            if status == "succeeded":
                output = body.get("output")
                if isinstance(output, list):
                    output_url = output[0]
                else:
                    output_url = output
                if not output_url:
                    raise RuntimeError("Replicate output URL missing")
                return self._download_to_path(output_url, os.path.join(job_dir, "video_replicate.mp4"))
            if status in {"failed", "canceled"}:
                raise RuntimeError(f"Replicate job failed: {body.get('error', status)}")
            time.sleep(3)
        raise RuntimeError("Replicate job timed out")

    def _generate_video_local(self, prompt: str, duration_seconds: int, job_dir: str) -> str:
        image_path = os.path.join(job_dir, "frame.png")
        video_path = os.path.join(job_dir, "video_local.mp4")

        r = self.session.post(
            f"{self.sd_host}/generate",
            json={"prompt": prompt},
            timeout=self.sd_generate_timeout_seconds,
        )
        r.raise_for_status()
        payload = r.json()
        image_b64 = payload.get("image_base64")
        if not image_b64:
            raise RuntimeError("Image service returned no image data")

        with open(image_path, "wb") as f:
            f.write(base64.b64decode(image_b64))

        _run_command(
            [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                image_path,
                "-vf",
                "scale=1280:720,format=yuv420p",
                "-t",
                str(duration_seconds),
                "-r",
                "24",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                video_path,
            ],
            "Failed to generate local video",
        )
        return video_path

    def _generate_audio(
        self,
        narration: str,
        duration_seconds: int,
        job_dir: str,
        use_elevenlabs: bool,
    ) -> tuple[str, str]:
        if use_elevenlabs:
            try:
                return self._generate_audio_elevenlabs(narration, job_dir), "elevenlabs"
            except Exception:
                pass

        if shutil.which("espeak-ng"):
            audio_path = os.path.join(job_dir, "audio_espeak.wav")
            _run_command(
                ["espeak-ng", "-s", "155", "-w", audio_path, narration],
                "Failed to generate local narration",
            )
            return audio_path, "espeak-ng"

        audio_path = os.path.join(job_dir, "audio_silent.m4a")
        _run_command(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t",
                str(duration_seconds),
                "-c:a",
                "aac",
                audio_path,
            ],
            "Failed to generate fallback audio",
        )
        return audio_path, "silent"

    def _generate_audio_elevenlabs(self, narration: str, job_dir: str) -> str:
        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
        model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip()
        if not api_key or not voice_id:
            raise RuntimeError("ElevenLabs not configured")

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": api_key,
        }
        payload = {
            "text": narration,
            "model_id": model_id,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        response = self.session.post(url, json=payload, headers=headers, timeout=120)
        response.raise_for_status()
        audio_path = os.path.join(job_dir, "audio_elevenlabs.mp3")
        with open(audio_path, "wb") as f:
            f.write(response.content)
        return audio_path

    def _mux_video_and_audio(self, video_path: str, audio_path: str, output_path: str) -> None:
        _run_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-i",
                audio_path,
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                output_path,
            ],
            "Failed to mux output video",
        )

    def _download_to_path(self, url: str, path: str) -> str:
        with self.session.get(url, stream=True, timeout=180) as response:
            response.raise_for_status()
            with open(path, "wb") as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
        return path
