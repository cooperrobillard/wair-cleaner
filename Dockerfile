# Dockerfile
FROM python:3.11-slim

# System deps for pillow/onnxruntime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo-dev zlib1g-dev libpng-dev ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Python deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App
COPY main.py .

# (Optional) tell Render what we expect, but Render scans $PORT anyway
EXPOSE 8000

# IMPORTANT: bind to 0.0.0.0 and the Render-provided $PORT
CMD ["bash", "-lc", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]