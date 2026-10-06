# AI-Video-Mini

Minimal AI video generator based on Wan2.1 T2V-1.3B.

Phase 1: Prompt -> short AI video (480p).

This intentionally excludes MPT, Pixabay, OpenRouter, Render, Kaggle, VACE, queues, databases, and multiple video models.

## Requirements
- Linux recommended
- NVIDIA GPU
- NVIDIA driver/CUDA working
- Python 3.10+
- ffmpeg
- Wan2.1 and the Wan2.1-T2V-1.3B model

## Wan2.1 setup

```bash
git clone https://github.com/Wan-Video/Wan2.1.git
cd Wan2.1
pip install -r requirements.txt
pip install -U "huggingface_hub[cli]"
huggingface-cli download Wan-AI/Wan2.1-T2V-1.3B --local-dir ./Wan2.1-T2V-1.3B
```

## App setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

export WAN_ROOT=/absolute/path/to/Wan2.1
export WAN_MODEL_DIR=/absolute/path/to/Wan2.1/Wan2.1-T2V-1.3B

uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000

First test:
A cinematic drone shot flying over a futuristic city at sunset, realistic lighting, smooth camera movement.

GitHub standard hosted runners do not provide the GPU needed by Wan2.1. The app is separated from GitHub Actions so a GPU runner/backend can be attached later without rewriting the UI.
