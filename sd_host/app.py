from fastapi import FastAPI
from pydantic import BaseModel
from diffusers import StableDiffusionPipeline
import torch
import os

app = FastAPI()

pipe = None

class GenerateRequest(BaseModel):
    prompt: str

@app.on_event("startup")
def load_model():
    global pipe
    model_id = "runwayml/stable-diffusion-v1-5"

    pipe = StableDiffusionPipeline.from_pretrained(
        model_id,
        torch_dtype=torch.float32,
        safety_checker=None
    )

    pipe = pipe.to("cpu")
    print("✅ Stable Diffusion loaded and ready")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/generate")
def generate(req: GenerateRequest):
    image = pipe(req.prompt).images[0]

    os.makedirs("/tmp/out", exist_ok=True)
    path = "/tmp/out/result.png"
    image.save(path)

    return {"image_path": path}
