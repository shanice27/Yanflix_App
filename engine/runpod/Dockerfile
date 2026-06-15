FROM runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04

WORKDIR /workspace

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ffmpeg libsndfile1 && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY engine/runpod/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# IndexTTS2
RUN git clone https://github.com/index-tts/IndexTTS2 /workspace/IndexTTS2 && \
    cd /workspace/IndexTTS2 && pip install --no-cache-dir -e .

# Download model checkpoints from HuggingFace at build time
# Set HF_TOKEN as a build arg if the repo is gated
ARG HF_TOKEN=""
RUN python -c "\
from huggingface_hub import snapshot_download; \
import os; \
snapshot_download('IndexTeam/IndexTTS2', local_dir='/workspace/IndexTTS2/checkpoints', token=os.environ.get('HF_TOKEN') or None)"

# Copy yanflix engine files needed on the pod
COPY engine/synthesis/synthesize_dub.py /workspace/yanflix/engine/synthesis/synthesize_dub.py
COPY engine/runpod/handler.py           /workspace/yanflix/engine/runpod/handler.py
COPY characters/                        /workspace/yanflix/characters/

# Expose FastAPI port
EXPOSE 8000

CMD ["python", "/workspace/yanflix/engine/runpod/handler.py", "--port", "8000"]
