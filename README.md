AI Video Generator & Editor

An end-to-end AI-powered video generation and editing platform combining modern web UI with a scalable backend and AI video pipeline.

✨ Features

AI-based video generation

Video editing pipeline

Background rendering jobs

Modular, scalable architecture

🏗 Architecture Overview

Frontend: Modern web UI (React / Next)

Backend API: Handles requests, job orchestration

AI Worker: Video generation & rendering (ffmpeg, ML models)

Docker: Used for reproducible environments and deployment

🐳 Why Docker?

This project is designed to evolve into a multi-service AI video pipeline.

Docker is used to:

Isolate heavy AI & video dependencies

Ensure reproducible builds across machines

Simplify deployment and demos

During development, the frontend runs locally for faster iteration.
Backend and AI workers are containerised for consistency.