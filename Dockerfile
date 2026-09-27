FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    HF_HOME=/runpod-volume/huggingface \
    XDG_CACHE_HOME=/runpod-volume/.cache \
    DIFFSYNTH_MODEL_BASE_PATH=/runpod-volume/models

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 \
        python3.12-venv \
        python3-pip \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && python3.12 -m venv "$VIRTUAL_ENV"

WORKDIR /opt/remove-ai-watermarks
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY handler.py ./handler.py

# Match the PyTorch CUDA wheel to the CUDA 12.8 base image before resolving the
# qwen-zimage extra, which includes the visible pixel and diffusion runtimes.
RUN python -m pip install --upgrade pip \
    && python -m pip install torch==2.9.1 torchvision==0.24.1 \
        --index-url https://download.pytorch.org/whl/cu128 \
    && python -m pip install '.[qwen-zimage]' 'runpod>=1.7,<2' \
    && python -c 'import torch; assert torch.version.cuda == "12.8"' \
    && python -c 'import runpod; assert callable(runpod.serverless.start)'

CMD ["python", "-u", "/opt/remove-ai-watermarks/handler.py"]
