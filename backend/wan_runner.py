import os
import subprocess
import uuid
from pathlib import Path

WAN_ROOT = Path(os.environ.get("WAN_ROOT", "")).expanduser().resolve()
MODEL_DIR = Path(os.environ.get(
    "WAN_MODEL_DIR",
    str(WAN_ROOT / "Wan2.1-T2V-1.3B")
)).expanduser().resolve()

OUTPUT_DIR = Path(os.environ.get(
    "OUTPUT_DIR",
    str(Path(__file__).resolve().parent.parent / "outputs")
)).expanduser().resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def generate(prompt: str):
    generate_py = WAN_ROOT / "generate.py"
    if not WAN_ROOT.exists():
        raise RuntimeError(f"WAN_ROOT does not exist: {WAN_ROOT}")
    if not generate_py.exists():
        raise RuntimeError(f"generate.py not found: {generate_py}")
    if not MODEL_DIR.exists():
        raise RuntimeError(f"Model directory not found: {MODEL_DIR}")

    job_id = uuid.uuid4().hex[:12]
    output = OUTPUT_DIR / f"{job_id}.mp4"

    command = [
        "python3", str(generate_py),
        "--task", "t2v-1.3B",
        "--size", "832*480",
        "--ckpt_dir", str(MODEL_DIR),
        "--offload_model", "True",
        "--t5_cpu",
        "--sample_shift", "8",
        "--sample_guide_scale", "6",
        "--prompt", prompt,
        "--save_file", str(output),
    ]

    result = subprocess.run(
        command,
        cwd=str(WAN_ROOT),
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout)[-6000:])

    if not output.exists() or output.stat().st_size < 1024:
        raise RuntimeError("Wan2.1 finished without a valid MP4.")

    return job_id, output
