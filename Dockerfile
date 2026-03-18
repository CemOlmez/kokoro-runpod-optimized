FROM pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    espeak-ng \
    libsndfile1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /workspace/requirements.txt
RUN pip install --no-cache-dir -r /workspace/requirements.txt

COPY app /workspace/app

# Pre-download model + voice files into image — eliminates HuggingFace download on cold start
RUN python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download('hexgrad/Kokoro-82M', local_dir='/workspace/models/Kokoro-82M')" \
    && rm -rf /root/.cache/pip

ENV KOKORO_LOCAL_MODEL_PATH=/workspace/models/Kokoro-82M

EXPOSE 8000

CMD sh -c "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
