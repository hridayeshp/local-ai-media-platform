import os
import torch
from diffusers import StableDiffusionPipeline

MODEL_ID = "runwayml/stable-diffusion-v1-5"

# FORCE CPU — no cuda, no mps, no autocast
pipe = StableDiffusionPipeline.from_pretrained(
    MODEL_ID,
    torch_dtype=torch.float32,
    safety_checker=None
)

pipe = pipe.to("cpu")

OUTPUT_DIR = "/outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def generate_image(prompt: str):
    image = pipe(prompt).images[0]

    filename = f"image_{abs(hash(prompt))}.png"
    path = os.path.join(OUTPUT_DIR, filename)

    image.save(path)
    return filename, path
