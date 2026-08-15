FROM denoland/deno:bin AS deno

FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JS runtime to evaluate YouTube's signature-decryption code;
# without one, downloads fail with HTTP 403. Copied from Deno's official
# binary-only image rather than curl|sh-ing an install script into the image.
COPY --from=deno /deno /usr/local/bin/deno

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# /data is a volume mount; pre-creating it here with the right ownership
# means Docker copies that ownership into the (initially empty) named
# volume on first mount, so appuser can write to it without a separate
# entrypoint/gosu dance.
RUN mkdir -p /data && chown -R appuser:appuser /data

ENV PYTHONPATH=/app/src \
    PODMACHINE_CONFIG=/config/config.yaml \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE 8000

CMD ["uvicorn", "podmachine.main:app", "--host", "0.0.0.0", "--port", "8000"]
