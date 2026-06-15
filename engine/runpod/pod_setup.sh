#!/bin/bash
# Run this once on a fresh RunPod Pod (PyTorch 2.x + CUDA 12 template)
# It installs IndexTTS2 and the handler, then starts the FastAPI server.

set -e

echo "=== Yanflix Pod Setup ==="

# 1. Clone IndexTTS2
if [ ! -d "/workspace/IndexTTS2" ]; then
  git clone https://github.com/index-tts/IndexTTS2 /workspace/IndexTTS2
fi
cd /workspace/IndexTTS2
pip install -e . -q

# 2. Download IndexTTS2 checkpoints from HuggingFace (first run only)
CKPT_DIR="/workspace/IndexTTS2/checkpoints"
mkdir -p "$CKPT_DIR"
if [ ! -f "$CKPT_DIR/config.yaml" ]; then
  pip install huggingface_hub -q
  python -c "
from huggingface_hub import snapshot_download
snapshot_download('IndexTeam/IndexTTS2', local_dir='$CKPT_DIR')
"
fi

# 3. Install handler deps
pip install fastapi uvicorn boto3 -q

# 4. Clone yanflix repo (uses YANFLIX_REPO env var, falls back to public GitHub)
REPO="${YANFLIX_REPO:-https://github.com/shanice27/Yanflix_App}"
if [ ! -d "/workspace/yanflix" ]; then
  echo "Cloning $REPO → /workspace/yanflix"
  git clone "$REPO" /workspace/yanflix
else
  echo "Updating /workspace/yanflix"
  git -C /workspace/yanflix pull --ff-only
fi

# 5. Start the handler on port 8888 (pre-exposed by PyTorch template; no template edit needed)
echo "=== Starting Pod handler on port 8888 ==="
echo "Public URL: https://$(hostname)-8888.proxy.runpod.net"
cd /workspace/yanflix
python engine/runpod/handler.py --port 8888
