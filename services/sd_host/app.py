import base64
import os
import threading
from io import BytesIO

import torch
from diffusers import StableDiffusionPipeline
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

pipe = None
pipe_lock = threading.Lock()
model_loaded = False

class GenerateRequest(BaseModel):
    prompt: str

@app.on_event("startup")
def load_model():
    global pipe, model_loaded
    model_id = os.getenv("SD_MODEL_ID", "runwayml/stable-diffusion-v1-5")
    device = os.getenv("SD_DEVICE", "cpu")
    torch_dtype = torch.float16 if device == "cuda" else torch.float32

    pipe = StableDiffusionPipeline.from_pretrained(
        model_id,
        torch_dtype=torch_dtype,
        safety_checker=None
    )
    pipe = pipe.to(device)

    model_loaded = True
    print("✅ Stable Diffusion loaded and ready")

@app.get("/health")
def health():
    status = "ok" if model_loaded else "loading"
    return {"status": status, "model_loaded": model_loaded}

@app.post("/generate")
def generate(req: GenerateRequest):
    if not model_loaded or pipe is None:
        raise HTTPException(status_code=503, detail="Model is still loading")

    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")

    with pipe_lock:
        image = pipe(prompt).images[0]

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    image_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return {"image_base64": image_b64}
