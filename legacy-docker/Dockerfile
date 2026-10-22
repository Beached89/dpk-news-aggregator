FROM python:3.11-slim

# No headless browser needed — KCNA article pages are server-rendered, so this
# image stays small (just Python + a few libs).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data

WORKDIR /app

# ffmpeg: downloads VOK's HLS video streams and feeds audio to Whisper ASR.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# CPU-only PyTorch (much smaller than the default CUDA wheel) for SimAlign word
# alignment. Must be >= 2.4 to satisfy current transformers.
RUN pip install --no-cache-dir torch==2.4.1 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Default: serve the web app. The scraper service overrides this command.
EXPOSE 8000
CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000"]
