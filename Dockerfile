# Dockerfile
FROM python:3.11-slim

# System deps for pillow/onnxruntime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo-dev zlib1g-dev libpng-dev ca-certificates patchelf \
    libgomp1 libstdc++6 libatomic1 \
  && rm -rf /var/lib/apt/lists/*

ENV OMP_NUM_THREADS=1
ENV MKL_THREADING_LAYER=GNU

# Python deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App
COPY main.py .

# (Optional) tell Render what we expect, but Render scans $PORT anyway
EXPOSE 8000

# Workaround for glibc execstack enforcement in some hosts
ENV GLIBC_TUNABLES=glibc.rtld.execstack=2

# Clear the execstack bit on onnxruntime's pybind .so so imports don't fail
RUN set -eux; \
  so_path="$(python - <<'PY'\nimport onnxruntime, pathlib\np = pathlib.Path(onnxruntime.__file__).parent / 'capi'\nprint(next(p.glob('onnxruntime_pybind11_state*.so')))\nPY\n)"; \
  echo "Patching $so_path"; \
  patchelf --clear-execstack "$so_path"

# IMPORTANT: bind to 0.0.0.0 and the Render-provided $PORT
CMD ["bash", "-lc", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
