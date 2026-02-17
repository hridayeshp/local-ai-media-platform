# Local AI Media Platform

Hybrid media generation platform with a static web frontend, a FastAPI backend, and a Stable Diffusion worker.

## Live Demo Site

- A GitHub Pages demo site is included under `docs/`.
- After pushing to `main`, GitHub Actions deploys it via `.github/workflows/pages.yml`.
- Enable Pages in repo settings if needed:
  - `Settings -> Pages -> Build and deployment -> Source: GitHub Actions`
- Add `docs/assets/demo.mp4` to show a real walkthrough video on the live page.

## Project Structure

```text
.
├── services/
│   ├── backend/      # FastAPI API and media pipeline services
│   ├── frontend/     # Static UI served by nginx
│   └── sd_host/      # Stable Diffusion inference service
├── uploads/          # Runtime user uploads (gitignored)
├── ai_outputs/       # Runtime generated outputs (gitignored)
└── docker-compose.yml
```

## Run

```bash
docker compose up --build
```

Open `http://localhost`.

## Phase 1 Features

- Prompt to video job queue (`POST /api/jobs/video`)
- Job polling with progress (`GET /api/jobs/{job_id}`)
- Download final MP4 (`GET /api/jobs/{job_id}/download`)
- Audio track generation for each video:
  - ElevenLabs (if configured)
  - Local `espeak-ng` fallback (zero-cost)
- Video generation source:
  - Replicate (if configured)
  - Local Stable Diffusion image-to-video fallback

## Phase 2 Features (Editor)

- Upload media assets (`POST /api/editor/assets/upload`)
- Asset library (`GET /api/editor/assets`)
- Timeline editor with:
  - trim (duration / in-point edits)
  - split
  - multi-track video/audio
  - text/captions
  - fade in/out transitions
- Timeline export to MP4 (`POST /api/editor/export`)
- Download exported edit (`GET /api/editor/exports/{export_id}/download`)

## Phase 3 Features (Interactive Timeline)

- Draggable clips on a visual timeline (move left/right)
- Clip handles for trim from both sides
- Snap-to-grid with configurable step (`0.1s`, `0.25s`, `0.5s`, `1s`)
- Zoomable timeline ruler (`px/s`)
- Audio waveform drawing for audio clips (decoded in browser from uploaded assets)
- Clip inspector for precise edits after drag operations
- Asset file endpoint for editor playback/waveforms:
  - `GET /api/editor/assets/{asset_id}/download`

## Services

- `frontend` (port `80`): UI and reverse proxy to backend via `/api/*`
- `backend` (port `8000`): API layer and orchestration
- `sd-host` (port `9000`): image generation worker

## Notes

- First image generation can take a while on CPU because the model loads on startup.
- `uploads/` and `ai_outputs/` are runtime directories and are intentionally excluded from git.
- Services now use healthchecks and startup ordering, so `frontend` and `backend` wait for dependencies.
- Hugging Face model cache is persisted in the `hf_cache` Docker volume to speed up subsequent runs.
- Generated job outputs are persisted in the `backend_outputs` Docker volume.

## Environment

Use `.env.example` as a base:

- `REPLICATE_API_TOKEN`, `REPLICATE_MODEL_VERSION` for cloud video generation
- `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` for cloud narration
- Without these keys, the system still works using local fallbacks.
