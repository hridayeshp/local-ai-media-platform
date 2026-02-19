import base64
import os
import threading
from io import BytesIO
from typing import Optional

import torch
from diffusers import StableDiffusionPipeline
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

app = FastAPI()

pipe = None
pipe_lock = threading.Lock()
model_loaded = False
active_device = "cpu"
active_dtype = "float32"

SD_DEFAULT_STEPS = int(os.getenv("SD_DEFAULT_STEPS", "24"))
SD_DEFAULT_GUIDANCE = float(os.getenv("SD_DEFAULT_GUIDANCE", "7.0"))
SD_DEFAULT_WIDTH = int(os.getenv("SD_DEFAULT_WIDTH", "512"))
SD_DEFAULT_HEIGHT = int(os.getenv("SD_DEFAULT_HEIGHT", "512"))
SD_MAX_STEPS = int(os.getenv("SD_MAX_STEPS", "50"))
SD_MAX_WIDTH = int(os.getenv("SD_MAX_WIDTH", "1024"))
SD_MAX_HEIGHT = int(os.getenv("SD_MAX_HEIGHT", "1024"))


def _resolve_device(requested_device: str) -> str:
    requested = (requested_device or "auto").strip().lower()
    if requested == "auto":
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    if requested in {"cuda", "mps", "cpu"}:
        if requested == "cuda" and not torch.cuda.is_available():
            return "cpu"
        if requested == "mps" and not (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()):
            return "cpu"
        return requested
    return "cpu"


class GenerateRequest(BaseModel):
    prompt: str
    num_inference_steps: int = Field(default=SD_DEFAULT_STEPS, ge=1, le=SD_MAX_STEPS)
    guidance_scale: float = Field(default=SD_DEFAULT_GUIDANCE, ge=1.0, le=20.0)
    width: int = Field(default=SD_DEFAULT_WIDTH, ge=256, le=SD_MAX_WIDTH)
    height: int = Field(default=SD_DEFAULT_HEIGHT, ge=256, le=SD_MAX_HEIGHT)
    seed: Optional[int] = Field(default=None, ge=0)

    @field_validator("width", "height")
    @classmethod
    def validate_dimensions(cls, value: int) -> int:
        if value % 8 != 0:
            raise ValueError("width and height must be divisible by 8")
        return value


@app.on_event("startup")
def load_model():
    global pipe, model_loaded, active_device, active_dtype
    model_id = os.getenv("SD_MODEL_ID", "runwayml/stable-diffusion-v1-5")
    device = _resolve_device(os.getenv("SD_DEVICE", "auto"))
    torch_dtype = torch.float16 if device == "cuda" else torch.float32

    if device == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.benchmark = True

    pipe = StableDiffusionPipeline.from_pretrained(
        model_id,
        torch_dtype=torch_dtype,
        use_safetensors=True,
        safety_checker=None,
    )
    pipe = pipe.to(device)
    pipe.enable_attention_slicing()
    pipe.enable_vae_slicing()

    active_device = device
    active_dtype = "float16" if torch_dtype == torch.float16 else "float32"
    model_loaded = True
    print(f"✅ Stable Diffusion loaded and ready on {active_device} ({active_dtype})")


@app.get("/health")
def health():
    status = "ok" if model_loaded else "loading"
    return {
        "status": status,
        "model_loaded": model_loaded,
        "device": active_device,
        "dtype": active_dtype,
        "defaults": {
            "steps": SD_DEFAULT_STEPS,
            "guidance": SD_DEFAULT_GUIDANCE,
            "width": SD_DEFAULT_WIDTH,
            "height": SD_DEFAULT_HEIGHT,
        },
    }


@app.post("/generate")
def generate(req: GenerateRequest):
    if not model_loaded or pipe is None:
        raise HTTPException(status_code=503, detail="Model is still loading")

    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    generator = None
    if req.seed is not None:
        generator_device = "cuda" if active_device == "cuda" else "cpu"
        generator = torch.Generator(device=generator_device).manual_seed(req.seed)

    inference_kwargs = {
        "prompt": prompt,
        "num_inference_steps": req.num_inference_steps,
        "guidance_scale": req.guidance_scale,
        "width": req.width,
        "height": req.height,
    }
    if generator is not None:
        inference_kwargs["generator"] = generator

    with pipe_lock:
        with torch.inference_mode():
            if active_device == "cuda":
                with torch.autocast(device_type="cuda", dtype=torch.float16):
                    image = pipe(**inference_kwargs).images[0]
            else:
                image = pipe(**inference_kwargs).images[0]

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    image_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return {"image_base64": image_b64}
