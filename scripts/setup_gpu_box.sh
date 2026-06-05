#!/usr/bin/env bash
# Set up a rented GPU box to run the bulk AI-video generation (Phase B).
#
# The two-phase pipeline does all cheap/API work locally (Asset Bible canonical
# images, per-shot keyframes, hero clips, the shot manifest). The ONLY thing that
# needs the GPU is the bulk Wan image-to-video batch. This script provisions the
# box for that batch + the assemble/render steps.
#
# Usage (on the box, Linux + NVIDIA CUDA):
#   bash scripts/setup_gpu_box.sh
#
# Then run:
#   python projects/<project>/script_v5/run_bulk_generation.py   # Phase B (Wan i2v)
#   python projects/<project>/script_v5/build_v6.py              # picks up AI segments
#   python projects/<project>/script_v5/render_v6.py             # final render
set -euo pipefail

echo "== OpenMontage GPU box setup =="

# 1. GPU / CUDA check
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || true
else
  echo "WARNING: nvidia-smi not found — no NVIDIA GPU detected. Wan i2v needs a CUDA GPU"
  echo "         (>=24 GB VRAM for Wan 2.1-14B, ~8 GB for the 1.3B tier)."
fi

# 2. ffmpeg (required for keyframe staging, concat, render)
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Installing ffmpeg ..."
  (sudo apt-get update && sudo apt-get install -y ffmpeg) || \
    echo "WARNING: could not auto-install ffmpeg — install it manually."
fi
ffmpeg -version | head -1 || true

# 3. Python deps (core + GPU video stack: torch/diffusers/transformers/accelerate)
PY="${PYTHON:-python3}"
echo "Installing Python dependencies with $PY ..."
$PY -m pip install --upgrade pip
$PY -m pip install -r requirements.txt
$PY -m pip install -r requirements-gpu.txt

# 4. Environment
echo ""
echo "== Required environment =="
cat <<'ENVHELP'
Export these (or put them in .env at the repo root):

  export VIDEO_GEN_LOCAL_ENABLED=true     # enables the local Wan provider
  export GEMINI_API_KEY=...               # Nano Banana 2 (keyframes; usually already done in prep)
  export KIE_API_KEY=...                  # hero shots (usually already generated in prep)
  export GOOGLE_API_KEY=...               # optional: Gemini semantic quality checks
  # optional: export VIDEO_GEN_LOCAL_MODEL=wan   # pin the local provider

Wan model weights download automatically on first generation (Hugging Face).
Pre-warm them by running run_bulk_generation.py on one shot, or:
  huggingface-cli download Wan-AI/Wan2.1-I2V-14B-480P-Diffusers
ENVHELP

# 5. Sanity check: is the local Wan provider available?
echo ""
echo "== Provider sanity check =="
VIDEO_GEN_LOCAL_ENABLED=true $PY - <<'PYCHECK' || true
from tools.tool_registry import registry
registry.discover()
wan = next((t for t in registry.get_by_capability("video_generation") if t.provider == "wan"), None)
print("wan discovered:", wan is not None)
if wan:
    print("wan status:", wan.get_status().value, "(AVAILABLE means weights + torch are ready)")
PYCHECK

echo ""
echo "Setup complete. Next: python projects/<project>/script_v5/run_bulk_generation.py"
