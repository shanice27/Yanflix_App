#!/bin/bash
# RunPod Pod setup — installs miniconda + Python 3.11 + IndexTTS2 + handler

set -e
echo "=== Yanflix Pod Setup ==="

# 1. Install miniconda if conda not in PATH
if ! command -v conda &>/dev/null; then
  echo "Installing Miniconda..."
  curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o /tmp/miniconda.sh
  bash /tmp/miniconda.sh -b -p /workspace/miniconda
  rm /tmp/miniconda.sh
  export PATH="/workspace/miniconda/bin:$PATH"
  conda init bash
  source /workspace/miniconda/etc/profile.d/conda.sh
else
  source "$(conda info --base)/etc/profile.d/conda.sh" 2>/dev/null || true
fi

export PATH="/workspace/miniconda/bin:$PATH"
source /workspace/miniconda/etc/profile.d/conda.sh 2>/dev/null || true

CONDA_ENV="yanflix"

# 2. Accept Anaconda TOS (required on first run)
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main 2>/dev/null || true
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r 2>/dev/null || true

# 3. Create Python 3.11 env if needed
if ! conda env list | grep -q "^$CONDA_ENV "; then
  echo "Creating conda env '$CONDA_ENV' with Python 3.11..."
  conda create -n $CONDA_ENV python=3.11 -y -q
fi

PY="conda run -n $CONDA_ENV --no-capture-output"

# 3. Clone + install IndexTTS2
if [ ! -d "/workspace/IndexTTS2" ]; then
  git clone https://github.com/index-tts/index-tts.git /workspace/IndexTTS2
fi
$PY pip install -e /workspace/IndexTTS2 -q

# 4. Download checkpoints (first run only)
CKPT_DIR="/workspace/IndexTTS2/checkpoints"
mkdir -p "$CKPT_DIR"
if [ ! -f "$CKPT_DIR/config.yaml" ]; then
  echo "Downloading IndexTTS-2 checkpoints (~4GB)..."
  $PY pip install huggingface_hub -q
  $PY python -c "
from huggingface_hub import snapshot_download
import os
snapshot_download('IndexTeam/IndexTTS-2', local_dir='$CKPT_DIR', token=os.environ.get('HF_TOKEN'))
"
fi

# 5. Install handler deps
$PY pip install fastapi uvicorn boto3 -q

# 6. Pull latest yanflix code
REPO="${YANFLIX_REPO:-https://github.com/shanice27/Yanflix_App}"
if [ ! -d "/workspace/yanflix" ]; then
  git clone "$REPO" /workspace/yanflix
else
  git -C /workspace/yanflix pull --ff-only
fi

# 7. Start handler on port 8888 (pre-exposed by PyTorch template)
echo "=== Starting handler on port 8888 ==="
echo "Public URL: https://$(hostname)-8888.proxy.runpod.net"
cd /workspace/yanflix
$PY python engine/runpod/handler.py --port 8888
