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

# 4. Clone yanflix engine (or copy pod_synth_handler.py manually)
if [ ! -d "/workspace/yanflix" ]; then
  echo "WARNING: /workspace/yanflix not found."
  echo "Upload engine/runpod/pod_synth_handler.py and engine/synthesis/synthesize_dub.py to /workspace/yanflix/"
  echo "Or set YANFLIX_REPO env var and we'll git clone it."
  if [ -n "$YANFLIX_REPO" ]; then
    git clone "$YANFLIX_REPO" /workspace/yanflix
  fi
fi

# 5. Start the handler
echo "=== Starting Pod handler on port 8000 ==="
cd /workspace/yanflix
python engine/runpod/pod_synth_handler.py --port 8000
