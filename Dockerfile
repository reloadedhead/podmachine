FROM denoland/deno:bin AS deno

FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JS runtime to evaluate YouTube's signature-decryption code;
# without one, downloads fail with HTTP 403. Copied from Deno's official
# binary-only image rather than curl|sh-ing an install script into the image.
COPY --from=deno /deno /usr/local/bin/deno

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV PYTHONPATH=/app/src \
    PODMACHINE_CONFIG=/config/config.yaml

EXPOSE 8000

CMD ["uvicorn", "podmachine.main:app", "--host", "0.0.0.0", "--port", "8000"]
