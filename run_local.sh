#!/usr/bin/env bash
set -e
: "${WAN_ROOT:?Set WAN_ROOT to your Wan2.1 directory}"
export WAN_MODEL_DIR="${WAN_MODEL_DIR:-$WAN_ROOT/Wan2.1-T2V-1.3B}"
python3 -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
