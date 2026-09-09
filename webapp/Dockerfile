# alarabia.chat - inference web app.
#
# Build from the repository root so the models and the package come along:
#     docker build -f webapp/Dockerfile -t alarabia-chat .
#
# The container is the outer half of the isolation story. The app already
# refuses hostile uploads and parses everything in memory; this image makes
# sure that even a successful parser exploit lands somewhere useless - no
# root, no writable filesystem, no shell, no capabilities.

FROM python:3.11-slim AS base

# fonts-noto-core supplies NotoNaskhArabic, which `render.find_font` picks up
# automatically and which renders diacritics far better than DejaVu.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-noto-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch: the models are small, and this keeps the image ~2 GB
# smaller than the CUDA build.
RUN pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cpu torch

COPY webapp/requirements.txt /tmp/webapp-requirements.txt
RUN pip install --no-cache-dir -r /tmp/webapp-requirements.txt

COPY pyproject.toml README.md LICENSE NOTICE /app/
COPY src/ /app/src/
# --no-deps keeps pip from pulling the CUDA torch build over the CPU wheel
# above. The cost is that the package's own dependencies are NOT installed,
# so every one of them must be named in webapp/requirements.txt. Importing
# the package here turns a forgotten dependency into a failed build instead
# of a container that starts cleanly and dies on the first model load.
RUN pip install --no-cache-dir --no-deps -e . \
 && python -c "import arabicmodels"

COPY models/ /app/models/
COPY webapp/ /app/webapp/

# Run as nobody. Nothing in /app needs to be writable at runtime: uploads are
# held in memory and results are cached in the process, never on disk.
USER 65534:65534

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ARABICWEB_PRELOAD=1

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health').read()"

CMD ["python", "-m", "webapp", "--host", "0.0.0.0", "--port", "8000"]
