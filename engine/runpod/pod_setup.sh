#!/bin/bash
# Run this once on a fresh RunPod Pod (PyTorch 2.x + CUDA 12 template)
# Uses conda Python 3.11 — IndexTTS2 requires Python >=3.8,<3.12

set -e

echo "=== Yanflix Pod Setup ==="

CONDA_ENV="yanflix"
PYTHON_BIN="$(conda run -n $CONDA_ENV which python 2>/dev/null || echo '')"

# 1. Create conda env with Python 3.11 if needed
if [ -z "$PYTHON_BIN" ]; then
  echo "Creating conda env '$CONDA_ENV' with Python 3.11..."
  conda create -n $CONDA_ENV python=3.11 -y -q
fi

# Helper: run commands inside the conda env
PY="conda run -n $CONDA_ENV --no-capture-output"

# 2. Clone + install IndexTTS2
if [ ! -d "/workspace/IndexTTS2" ]; then
  git clone https://github.com/index-tts/index-tts.git /workspace/IndexTTS2
fi
cd /workspace/IndexTTS2
$PY pip install -e . -q

# 3. Download checkpoints from HuggingFace (first run only)
CKPT_DIR="/workspace/IndexTTS2/checkpoints"
mkdir -p "$CKPT_DIR"
if [ ! -f "$CKPT_DIR/config.yaml" ]; then
  echo "Downloading IndexTTS-2 checkpoints from HuggingFace..."
  $PY pip install huggingface_hub -q
  $PY python -c "
from huggingface_hub import snapshot_download
import os
snapshot_download('IndexTeam/IndexTTS-2', local_dir='$CKPT_DIR', token=os.environ.get('HF_TOKEN'))
"
fi

# 4. Install handler deps
$PY pip install fastapi uvicorn boto3 -q

# 5. Pull latest yanflix code
REPO="${YANFLIX_REPO:-https://github.com/shanice27/Yanflix_App}"
if [ ! -d "/workspace/yanflix" ]; then
  git clone "$REPO" /workspace/yanflix
else
  git -C /workspace/yanflix pull --ff-only
fi

# 6. Start handler on port 8888 (pre-exposed by PyTorch template)
echo "=== Starting Pod handler on port 8888 ==="
echo "Public URL: https://$(hostname)-8888.proxy.runpod.net"
cd /workspace/yanflix
$PY python engine/runpod/handler.py --port 8888
