import base64
import os
import threading
from io import BytesIO
from typing import Optional

import torch
from diffusers import StableDiffusionPipeline
from fastapi import FastAPI, HTTPException
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field, field_validator

app = FastAPI()

pipe = None
pipe_lock = threading.Lock()
model_loaded = False
model_error = None
active_device = "cpu"
active_dtype = "float32"

SD_MOCK = os.getenv("SD_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}
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


def _mock_image(prompt: str, width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height), color=(24, 28, 36))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        shade = int(24 + (y / max(1, height - 1)) * 80)
        draw.line([(0, y), (width, y)], fill=(shade, 42, 88))
    draw.rectangle((24, 24, width - 24, height - 24), outline=(180, 220, 255), width=4)
    draw.text((40, 44), "SD_MOCK image", fill=(255, 255, 255))
    draw.text((40, 76), prompt[:120], fill=(230, 235, 245))
    return image


def _load_model_sync() -> None:
    global pipe, model_loaded, model_error, active_device, active_dtype
    if SD_MOCK:
        active_device = "mock"
        active_dtype = "mock"
        model_loaded = True
        model_error = None
        print("Stable Diffusion mock mode enabled")
        return

    model_id = os.getenv("SD_MODEL_ID", "runwayml/stable-diffusion-v1-5")
    device = _resolve_device(os.getenv("SD_DEVICE", "auto"))
    torch_dtype = torch.float16 if device == "cuda" else torch.float32

    try:
        if device == "cuda":
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.benchmark = True

        loaded_pipe = StableDiffusionPipeline.from_pretrained(
            model_id,
            torch_dtype=torch_dtype,
            use_safetensors=True,
            safety_checker=None,
        )
        loaded_pipe = loaded_pipe.to(device)
        loaded_pipe.enable_attention_slicing()
        loaded_pipe.enable_vae_slicing()

        pipe = loaded_pipe
        active_device = device
        active_dtype = "float16" if torch_dtype == torch.float16 else "float32"
        model_loaded = True
        model_error = None
        print(f"Stable Diffusion loaded and ready on {active_device} ({active_dtype})")
    except Exception as exc:
        model_loaded = False
        model_error = str(exc)
        print(f"Stable Diffusion failed to load: {model_error}")


@app.on_event("startup")
def startup_event():
    threading.Thread(target=_load_model_sync, daemon=True).start()


@app.get("/health")
def health():
    status = "ok" if model_loaded else "error" if model_error else "loading"
    return {
        "status": status,
        "model_loaded": model_loaded,
        "model_error": model_error,
        "device": active_device,
        "dtype": active_dtype,
        "mock": SD_MOCK,
        "defaults": {
            "steps": SD_DEFAULT_STEPS,
            "guidance": SD_DEFAULT_GUIDANCE,
            "width": SD_DEFAULT_WIDTH,
            "height": SD_DEFAULT_HEIGHT,
        },
    }


@app.post("/generate")
def generate(req: GenerateRequest):
    if model_error:
        raise HTTPException(status_code=503, detail=f"Model failed to load: {model_error}")
    if not model_loaded:
        raise HTTPException(status_code=503, detail="Model is still loading")

    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    if SD_MOCK:
        image = _mock_image(prompt, req.width, req.height)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        image_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return {"image_base64": image_b64}

    if pipe is None:
        raise HTTPException(status_code=503, detail="Model is still loading")

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
