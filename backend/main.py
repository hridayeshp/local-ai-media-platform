from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
import time

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SD_HOST = "http://sd-host:9000"


def wait_for_sd_on_demand(timeout=300):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{SD_HOST}/health", timeout=2)
            if r.status_code == 200:
                return
        except:
            pass
        time.sleep(2)
    raise HTTPException(
        status_code=503,
        detail="Image service is still starting. Try again in a minute."
    )


@app.get("/health")
def health():
    return {"status": "backend-ok"}


@app.post("/generate-image")
def generate_image(data: dict):
    # 🔥 WAIT ONLY WHEN USER REQUESTS
    wait_for_sd_on_demand()

    try:
        r = requests.post(
            f"{SD_HOST}/generate",
            json=data,
            timeout=600
        )
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
